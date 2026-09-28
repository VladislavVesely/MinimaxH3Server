"""SSH tests substitute only socket/Paramiko network objects, not policy or probes."""
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
    assert network.socket_timeout == network.handshake_timeouts[0] == 10
    connection.close()
    assert network.sockets[0].closed and network.transports[0].closed


def test_persisted_host_key_owner_only_and_changed_key_blocked(tmp_path):
    from h3server.controller.ssh import ControllerError
    network, connection = trusted_connection(tmp_path)
    path = tmp_path / "local" / "known_hosts"
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


def test_command_timeout_and_output_limits_close_channel(tmp_path):
    network, connection = trusted_connection(tmp_path)
    network.hang = True
    result = connection(("uname", "-s"), timeout=0.01)
    assert result.error == "timeout"
    assert network.transports[0].channels[-1].closed
    network.hang = False
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
    assert client.get_channel().timeout == 10
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
