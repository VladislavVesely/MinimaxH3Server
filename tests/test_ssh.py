"""SSH tests substitute only socket/Paramiko network objects, not policy or probes."""
import os
import shlex
import socket
import stat
import threading
import time

import paramiko
import pytest


class FakeSocket:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeChannel:
    def __init__(self, network):
        self.network = network
        self.closed = False
        self.output = b""
        self.stderr = b""
        self.closed_event = threading.Event()

    def get_name(self):
        return "fake-network-channel"

    def settimeout(self, timeout):
        self.timeout = timeout

    def exec_command(self, command):
        self.network.commands.append(command)
        self.network.thread_ids.append(threading.get_ident())
        if self.network.block_exec:
            self.closed_event.wait(0.2)
            if self.closed:
                raise paramiko.SSHException("closed while waiting for exec acknowledgement")
        if self.network.command_error:
            raise self.network.command_error
        self.output = self.network.outputs.get(command, b"")

    def shutdown_write(self):
        pass

    def invoke_subsystem(self, subsystem):
        self.network.subsystems.append(subsystem)
        # SSH_FXP_VERSION packet from an SFTP v3 peer.
        self.output = b"\x00\x00\x00\x05\x02\x00\x00\x00\x03"

    def send(self, value):
        self.network.sftp_packets.append(bytes(value))
        return len(value)

    def recv_ready(self):
        return bool(self.output)

    def recv(self, size):
        value, self.output = self.output[:size], self.output[size:]
        return value

    def recv_stderr_ready(self):
        return bool(self.stderr)

    def recv_stderr(self, size):
        value, self.stderr = self.stderr[:size], self.stderr[size:]
        return value

    def exit_status_ready(self):
        return not self.network.hang

    def recv_exit_status(self):
        return 0

    def close(self):
        self.closed = True
        self.closed_event.set()


class FakeTransport:
    def __init__(self, network, sock):
        self.network = network
        self.sock = sock
        self.closed = False
        self.authenticated = False
        self.channels = []

    def set_log_channel(self, name):
        self.log_channel = name

    def start_client(self, timeout):
        self.network.handshake_timeouts.append(timeout)
        if self.network.handshake_error:
            raise self.network.handshake_error

    def get_remote_server_key(self):
        return self.network.key

    def is_active(self):
        return not self.closed

    def is_authenticated(self):
        return self.authenticated

    def auth_password(self, username, password, fallback):
        self.network.auth_calls.append((username, password, fallback))
        if self.network.auth_error:
            raise self.network.auth_error
        self.authenticated = True

    def open_session(self, timeout):
        channel = FakeChannel(self.network)
        self.channels.append(channel)
        return channel

    def close(self):
        self.closed = True
        for channel in self.channels:
            channel.close()


class Network:
    def __init__(self):
        self.key = paramiko.RSAKey.generate(1024)
        self.sockets = []
        self.transports = []
        self.commands = []
        self.auth_calls = []
        self.handshake_timeouts = []
        self.thread_ids = []
        self.handshake_error = None
        self.auth_error = None
        self.command_error = None
        self.hang = False
        self.block_exec = False
        self.subsystems = []
        self.sftp_packets = []
        self.outputs = {"printf '%s' \"$HOME\"": b"/home/test"}

    def socket(self, address, timeout):
        self.address = address
        self.socket_timeout = timeout
        sock = FakeSocket()
        self.sockets.append(sock)
        return sock

    def transport(self, sock):
        transport = FakeTransport(self, sock)
        self.transports.append(transport)
        return transport

    def connection(self, state_dir):
        from h3server.controller.ssh import SSHConnection
        return SSHConnection(state_dir, socket_factory=self.socket, transport_factory=self.transport)


def trusted_connection(tmp_path):
    network = Network()
    connection = network.connection(tmp_path / "local")
    challenge = connection.connect("example.org", 22, "test")
    connection.approve(challenge["fingerprint"])
    connection.authenticate("test-secret")
    return network, connection


def test_handshake_never_authenticates_until_trust_and_explicit_auth(tmp_path):
    from h3server.controller.ssh import ControllerError
    network = Network()
    connection = network.connection(tmp_path / "local")
    challenge = connection.connect("example.org", 2222, "test")
    assert challenge["state"] == "awaiting_approval"
    assert challenge["fingerprint"].startswith("SHA256:")
    assert network.auth_calls == []
    with pytest.raises(ControllerError, match="trust_required"):
        connection.authenticate("test-secret")
    with pytest.raises(ControllerError, match="fingerprint_mismatch"):
        connection.approve("SHA256:wrong")
    connection.approve(challenge["fingerprint"])
    assert network.auth_calls == []
    connection.authenticate("test-secret")
    assert network.auth_calls == [("test", "test-secret", False)]
    assert connection.status()["state"] == "authenticated"
    assert network.socket_timeout == 10
    assert 0 < network.handshake_timeouts[0] <= 10
    connection.close()
    assert network.sockets[0].closed and network.transports[0].closed


def test_persisted_host_key_owner_only_and_changed_key_blocked(tmp_path):
    from h3server.controller.ssh import ControllerError
    network, connection = trusted_connection(tmp_path)
    path = tmp_path / "local" / "known_hosts"
    if os.name == 'nt':
        from test_windows_trust import assert_private
        assert_private(path)
        assert_private(path.parent)
    else:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert "test-secret" not in path.read_text()
    connection.close()
    connection = network.connection(path.parent)
    assert connection.connect("example.org", 22, "test")["state"] == "awaiting_authentication"
    connection.close()
    network.key = paramiko.RSAKey.generate(1024)
    with pytest.raises(ControllerError, match="host_key_changed"):
        connection.connect("example.org", 22, "test")
    assert connection.status()["state"] == "disconnected"
    assert network.transports[-1].closed and network.sockets[-1].closed
    assert len(network.auth_calls) == 1


@pytest.mark.parametrize("host,port,user", [
    ("-option", 22, "test"), ("host;id", 22, "test"), (" host", 22, "test"),
    ("host\n", 22, "test"), ("https://host", 22, "test"), ("host", 0, "test"),
    ("host", 65536, "test"), ("host", True, "test"), ("host", 22, "-root"),
    ("host", 22, "a b"), ("host", 22, "a\n"),
])
def test_invalid_target_rejected_before_network(tmp_path, host, port, user):
    from h3server.controller.ssh import ControllerError
    network = Network()
    with pytest.raises(ControllerError, match="invalid_target"):
        network.connection(tmp_path).connect(host, port, user)
    assert network.sockets == []


@pytest.mark.parametrize("host", ["example.org", "192.0.2.1", "2001:db8::1"])
def test_valid_hosts(tmp_path, host):
    network = Network()
    connection = network.connection(tmp_path)
    connection.connect(host, 22, "test-user")
    connection.close()


@pytest.mark.parametrize("stage", ["handshake", "auth"])
def test_errors_are_generic_and_close_resources(tmp_path, stage, caplog):
    from h3server.controller.ssh import ControllerError
    network = Network()
    setattr(network, stage + "_error", paramiko.SSHException("test-secret remote sensitive details"))
    connection = network.connection(tmp_path)
    with pytest.raises(ControllerError) as error:
        challenge = connection.connect("example.org", 22, "test")
        connection.approve(challenge["fingerprint"])
        connection.authenticate("test-secret")
    assert "test-secret" not in str(error.value) + repr(connection) + caplog.text
    assert error.value.__suppress_context__
    assert network.sockets[-1].closed and network.transports[-1].closed


def test_readonly_commands_preserve_argv_and_validate_paths(tmp_path):
    network, connection = trusted_connection(tmp_path)
    path = "/home/a '$(touch /tmp/no); #"
    assert connection(("df", "-Pk", "--", path), timeout=1).succeeded
    assert shlex.split(network.commands[-1]) == ["LC_ALL=C", "df", "-Pk", "--", path]
    for command in [("sh", "-c", "id"), ("df", "-Pk", "--", "relative"), ("df", "-Pk", "--", "/bad\n")]:
        with pytest.raises(ValueError):
            connection(command, timeout=1)
    for timeout in [0, -1, float("nan"), 31, True]:
        with pytest.raises(ValueError):
            connection(("uname", "-s"), timeout=timeout)
    assert all(channel.closed for channel in network.transports[0].channels)
    connection.close()


def test_home_is_fixed_command_and_absolute_not_evaluated(tmp_path):
    from h3server.controller.ssh import ControllerError
    network, connection = trusted_connection(tmp_path)
    network.outputs["printf '%s' \"$HOME\""] = b"/home/test '$(id)"
    assert connection.remote_home() == "/home/test '$(id)"
    for value in [b"", b"relative", b"/home/test\n", b"/home/\x00", b"/home/\xff"]:
        network.outputs["printf '%s' \"$HOME\""] = value
        with pytest.raises(ControllerError, match="invalid_remote_home"):
            connection.remote_home()
    connection.close()


def test_command_timeout_closes_connection_and_channel(tmp_path):
    network, connection = trusted_connection(tmp_path)
    network.hang = True
    result = connection(("uname", "-s"), timeout=0.01)
    assert result.error == "timeout"
    assert network.transports[0].channels[-1].closed
    assert network.transports[0].closed and network.sockets[0].closed
    assert connection.status()["state"] == "disconnected"


def test_command_output_limits_close_channel(tmp_path):
    network, connection = trusted_connection(tmp_path)
    network.outputs["LC_ALL=C uname -s"] = b"x" * (1024 * 1024 + 1)
    result = connection(("uname", "-s"), timeout=1)
    assert result.error == "output-limit"
    assert network.transports[0].channels[-1].closed
    network.command_error = socket.timeout("test-secret")
    assert "test-secret" not in repr(connection(("uname", "-s"), timeout=1))
    connection.close()


def test_untrusted_connection_cannot_run_commands_or_sftp(tmp_path):
    from h3server.controller.ssh import ControllerError
    network = Network()
    connection = network.connection(tmp_path)
    connection.connect("example.org", 22, "test")
    for action in [lambda: connection(("uname", "-s"), timeout=1), connection.remote_home, connection.open_sftp]:
        with pytest.raises(ControllerError, match="authentication_required"):
            action()
    connection.close()


@pytest.mark.skipif(os.name == 'nt', reason='Windows reparse/hardlink cases use native junctions without symlink privilege')
def test_symlink_trust_file_rejected_without_modifying_target(tmp_path):
    from h3server.controller.ssh import ControllerError
    target = tmp_path / "other"
    target.write_text("untouched")
    state_dir = tmp_path / "local"
    state_dir.mkdir()
    (state_dir / "known_hosts").symlink_to(target)
    network = Network()
    with pytest.raises(ControllerError, match="state_unavailable"):
        network.connection(state_dir).connect("example.org", 22, "test")
    assert target.read_text() == "untouched"
    assert network.auth_calls == []


def test_exec_acknowledgement_has_hard_deadline(tmp_path):
    network, connection = trusted_connection(tmp_path)
    network.block_exec = True
    start = time.monotonic()
    result = connection(("uname", "-s"), timeout=0.02)
    elapsed = time.monotonic() - start
    assert result.error == "timeout"
    assert elapsed < 0.15
    assert network.transports[0].channels[-1].closed
    connection.close()


def test_real_sftp_protocol_handshake_on_authenticated_channel(tmp_path):
    network, connection = trusted_connection(tmp_path)
    client = connection.open_sftp()
    assert isinstance(client, paramiko.SFTPClient)
    assert network.subsystems == ["sftp"]
    assert network.sftp_packets == [b"\x00\x00\x00\x05\x01\x00\x00\x00\x03"]
    assert 0 < client.get_channel().timeout <= 10
    connection.close()
    assert client.get_channel().closed


def test_trust_is_scoped_to_host_and_port(tmp_path):
    network, connection = trusted_connection(tmp_path)
    connection.close()
    assert connection.connect("example.org", 2222, "test")["state"] == "awaiting_approval"
    connection.close()
    assert connection.connect("other.example", 22, "test")["state"] == "awaiting_approval"
    connection.close()


def test_changed_key_algorithm_also_blocks(tmp_path):
    from h3server.controller.ssh import ControllerError
    network, connection = trusted_connection(tmp_path)
    connection.close()
    network.key = paramiko.ECDSAKey.generate()
    with pytest.raises(ControllerError, match="host_key_changed"):
        connection.connect("example.org", 22, "test")
    assert len(network.auth_calls) == 1


class MemorySocket:
    """A byte-stream peer, not an OS socket; real Paramiko runs at both ends."""

    def __init__(self):
        self.peer = None
        self.incoming = bytearray()
        self.condition = threading.Condition()
        self._closed = False
        self.timeout = 0.1
        self.stall_after = None
        self.writes = 0
        self.write_timeouts = 0

    def settimeout(self, timeout):
        self.timeout = timeout

    def send(self, data):
        if self._closed:
            raise OSError("closed fake socket")
        if self.stall_after is not None and self.writes >= self.stall_after:
            time.sleep(0.005)
            self.write_timeouts += 1
            raise socket.timeout("fake peer is not accepting writes")
        self.writes += 1
        with self.peer.condition:
            self.peer.incoming.extend(data)
            self.peer.condition.notify_all()
        return len(data)

    def recv(self, size):
        with self.condition:
            if not self.condition.wait_for(lambda: self.incoming or self._closed, self.timeout):
                raise socket.timeout()
            data = bytes(self.incoming[:size])
            del self.incoming[:size]
            return data

    def shutdown(self, how):
        self.close()

    def close(self):
        for endpoint in (self, self.peer):
            with endpoint.condition:
                endpoint._closed = True
                endpoint.condition.notify_all()


class MemorySSHServer(paramiko.ServerInterface):
    """Test-only protocol peer; never executes the received command."""

    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED

    def check_channel_exec_request(self, channel, command):
        channel.send_exit_status(0)
        return True


@pytest.fixture
def memory_ssh(tmp_path):
    from h3server.controller.ssh import SSHConnection
    client_socket, server_socket = MemorySocket(), MemorySocket()
    client_socket.peer, server_socket.peer = server_socket, client_socket
    server = paramiko.Transport(server_socket)
    server.add_server_key(paramiko.RSAKey.generate(1024))
    server.set_subsystem_handler("sftp", paramiko.SFTPServer)
    server.start_server(event=threading.Event(), server=MemorySSHServer())
    transports = []

    def transport_factory(sock):
        transport = paramiko.Transport(sock)
        transports.append(transport)
        return transport

    connection = SSHConnection(tmp_path / "memory", socket_factory=lambda *args, **kwargs: client_socket,
                               transport_factory=transport_factory)
    try:
        challenge = connection.connect("memory.example", 22, "test")
        connection.approve(challenge["fingerprint"])
        yield connection, client_socket, server_socket, server, transports[0]
    finally:
        # Out-of-band teardown also bounds the deliberately failing red tests.
        client_socket.close()
        for transport in [*transports, server]:
            transport.close()
            transport.join(timeout=1)
            assert not transport.is_alive()
        connection.close()


def run_bounded_operation(action, sock, transport, *, limit):
    """Observe deadline failure without hanging pytest or leaving worker threads."""
    results = []
    errors = []
    done = threading.Event()

    def run():
        try:
            results.append(action())
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        assert done.wait(limit), "SSH operation exceeded deadline while transport IO was stalled"
        return results, errors
    finally:
        if not done.is_set():
            sock.close()
            transport.close()
        worker.join(timeout=1)
        assert not worker.is_alive()


@pytest.mark.parametrize("successful_writes", [0, 1, 3], ids=["channel-open", "exec", "channel-close"])
def test_real_paramiko_write_retries_obey_probe_deadline(memory_ssh, successful_writes):
    connection, sock, _, _, transport = memory_ssh
    connection.authenticate("test-secret")
    sock.writes = 0
    sock.stall_after = successful_writes
    results, errors = run_bounded_operation(lambda: connection(("uname", "-s"), timeout=0.05),
                                            sock, transport, limit=0.8)
    assert not errors
    assert results[0].error == "timeout"  # even if only cleanup stalled
    assert sock.write_timeouts > 1
    assert sock._closed and not transport.is_active()
    assert connection.status()["state"] == "disconnected"
    assert connection.close()["state"] == "disconnected"


def test_real_paramiko_stalled_rekey_obeys_probe_deadline(memory_ssh):
    connection, sock, server_socket, server, transport = memory_ssh
    connection.authenticate("test-secret")
    server_socket.writes = 0
    server_socket.stall_after = 1  # send KEXINIT, then withhold the key-exchange response
    rekey_errors = []

    def rekey():
        try:
            server.renegotiate_keys()
        except Exception as exc:
            rekey_errors.append(exc)

    rekey_worker = threading.Thread(target=rekey, daemon=True)
    rekey_worker.start()
    try:
        until = time.monotonic() + 1
        while transport.clear_to_send.is_set() and time.monotonic() < until:
            time.sleep(0.005)
        assert not transport.clear_to_send.is_set(), "real Paramiko did not enter rekey wait"
        results, errors = run_bounded_operation(lambda: connection(("uname", "-s"), timeout=0.05),
                                                sock, transport, limit=0.8)
        assert not errors
        assert results[0].error == "timeout"
        assert connection.status()["state"] == "disconnected"
    finally:
        server_socket.close()
        server.close()
        rekey_worker.join(timeout=1)
        assert not rekey_worker.is_alive()


@pytest.mark.parametrize("operation", ["authenticate", "sftp"])
def test_real_paramiko_write_retries_obey_auth_and_sftp_deadlines(memory_ssh, operation):
    from h3server.controller.ssh import ControllerError
    connection, sock, _, _, transport = memory_ssh
    if operation == "sftp":
        connection.authenticate("test-secret")
    sock.writes = 0
    sock.stall_after = 0
    action = connection.open_sftp if operation == "sftp" else lambda: connection.authenticate("test-secret")
    results, errors = run_bounded_operation(action, sock, transport, limit=11)
    assert not results
    assert len(errors) == 1 and isinstance(errors[0], ControllerError)
    assert errors[0].code == ("sftp_unavailable" if operation == "sftp" else "authentication_failed")
    assert sock.write_timeouts > 1
    assert connection.status()["state"] == "disconnected"


def test_real_paramiko_disconnect_aborts_io_before_sftp_close(memory_ssh):
    connection, sock, _, _, transport = memory_ssh
    connection.authenticate("test-secret")
    client = connection.open_sftp()
    sock.writes = 0
    sock.stall_after = 0
    results, errors = run_bounded_operation(connection.close, sock, transport, limit=0.8)
    assert not errors
    assert results[0]["state"] == "disconnected"
    assert client.get_channel().closed
    assert sock.write_timeouts == 0


def test_real_paramiko_success_cancels_deadline_without_disconnect(memory_ssh):
    connection, sock, _, _, transport = memory_ssh
    connection.authenticate("test-secret")
    result = connection(("uname", "-s"), timeout=0.1)
    assert result.succeeded
    time.sleep(0.15)
    assert connection.status()["state"] == "authenticated"
    assert transport.is_active() and not sock._closed
