"""Trust-before-authentication SSH and bounded, allowlisted read-only commands."""

from base64 import b64encode
from contextlib import contextmanager
from functools import wraps
import hashlib
import hmac
import ipaddress
import logging
import math
import os
from pathlib import Path
import re
import secrets
import shlex
import socket
import stat
import tempfile
import threading
import time

import paramiko

from h3server.probe import CommandResult, PROBE_COMMANDS


class ControllerError(Exception):
    """Only stable, local error codes cross the transport boundary."""

    def __init__(self, code: str, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def _locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call


def _target(host: str, port: int, username: str) -> str:
    valid_host = isinstance(host, str) and 0 < len(host) <= 253
    if valid_host:
        try:
            ipaddress.ip_address(host)
            valid_host = "%" not in host
        except ValueError:
            valid_host = all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                             for label in host.split("."))
    if (not valid_host or type(port) is not int or not 1 <= port <= 65535
            or not isinstance(username, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}\$?", username)):
        raise ControllerError("invalid_target", 422)
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return host.lower()


def _absolute_path(path: str) -> bool:
    return (isinstance(path, str) and path.startswith("/") and len(path) <= 4096
            and not any(ord(c) < 32 or ord(c) == 127 for c in path))


def _fingerprint(key: paramiko.PKey) -> str:
    return "SHA256:" + b64encode(hashlib.sha256(key.asbytes()).digest()).decode("ascii").rstrip("=")


def _close_quietly(resource) -> None:
    try:
        resource.close()
    except Exception:
        pass


def _abort_transport(sock, transport) -> None:
    # Interrupt socket IO before closing Paramiko. Channel.close() itself sends
    # packets and cannot interrupt packetizer write retries or rekey waits.
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        _close_quietly(sock)
    if transport is not None:
        _close_quietly(transport)


def _connect_socket(factory, address, timeout):
    """Bound DNS as well as socket connect; late sockets are closed, never reused.

    Python cannot interrupt an OS resolver call. The daemon worker may finish
    later, but it owns no controller state and cannot authenticate or launch work.
    """
    done, guard = threading.Event(), threading.Lock()
    result, abandoned = [None], [False]
    def connect():
        sock = None
        try:
            sock = factory(address, timeout=timeout)
        except Exception:
            pass
        with guard:
            if abandoned[0]:
                if sock is not None:
                    _close_quietly(sock)
            else:
                result[0] = sock
            done.set()
    threading.Thread(target=connect, name="h3-ssh-connect", daemon=True).start()
    done.wait(timeout)
    with guard:
        if not done.is_set():
            abandoned[0] = True
            raise TimeoutError("connection deadline expired")
        if result[0] is None:
            raise OSError("connection failed")
        return result[0]


class _TransportDeadline:
    """An IO watchdog independent of the connection lock, armed through cleanup."""

    def __init__(self, sock, transport, timeout: float):
        self._socket = sock
        self._transport = transport
        self.expires_at = time.monotonic() + timeout
        self.expired = threading.Event()
        self._timer = threading.Timer(timeout, self._expire)
        self._timer.daemon = True

    def _expire(self):
        self.expired.set()
        _abort_transport(self._socket, self._transport)

    def __enter__(self):
        self._timer.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._timer.cancel()
        # No old callback may survive release of the connection lock. Only
        # captured IO objects are touched by the watchdog, never controller state.
        self._timer.join()
        if time.monotonic() >= self.expires_at and not self.expired.is_set():
            self._expire()

    def remaining(self) -> float:
        return max(0.001, self.expires_at - time.monotonic())


class KnownHosts:
    """Dedicated trust store; never imports account credentials or SSH config.

    POSIX uses ownership/mode checks; Windows uses native protected owner ACLs.
    Both implementations fail closed on unsafe files or unsupported filesystems.
    """

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.path = self.directory / "known_hosts"

    @staticmethod
    def _parse(lines):
        keys = paramiko.HostKeys()
        for line in lines:
            if not line.strip() or line.startswith('#'):
                continue
            entry = paramiko.hostkeys.HostKeyEntry.from_line(line.strip())
            if entry is None or entry.key is None:
                raise ValueError('invalid trust file')
            for host in entry.hostnames:
                if keys.lookup(host) is not None and entry.key.get_name() in keys[host]:
                    raise ValueError('duplicate trust entry')
                keys.add(host, entry.key.get_name(), entry.key)
        return keys

    def load(self) -> paramiko.HostKeys:
        try:
            if os.name == 'nt':
                from h3server.controller import windows_trust
                return self._parse(windows_trust.read(self.directory).splitlines())
            if os.name != "posix" or self.directory.is_symlink():
                raise OSError("private state directory unavailable")
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory_stat = self.directory.stat()
            if directory_stat.st_uid != os.getuid() or not stat.S_ISDIR(directory_stat.st_mode):
                raise OSError("private state directory unavailable")
            self.directory.chmod(0o700)
            keys = paramiko.HostKeys()
            try:
                fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                return keys
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                info = os.fstat(stream.fileno())
                if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise OSError("invalid trust file")
                os.fchmod(stream.fileno(), 0o600)
                return self._parse(stream)
        except Exception:
            raise ControllerError("state_unavailable", 503) from None

    def approve(self, host: str, key: paramiko.PKey) -> None:
        keys = self.load()
        known = keys.lookup(host)
        if known is not None and not keys.check(host, key):
            raise ControllerError("host_key_changed")
        keys.add(host, key.get_name(), key)
        temporary = None
        try:
            if os.name == 'nt':
                from h3server.controller import windows_trust
                text = ''.join(f'{name} {algorithm} {stored.get_base64()}\n'
                               for name in keys for algorithm, stored in keys[name].items())
                windows_trust.write(self.directory, text)
                return
            fd, temporary = tempfile.mkstemp(prefix=".known_hosts-", dir=self.directory)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                for name in keys:
                    for algorithm, stored in keys[name].items():
                        stream.write(f"{name} {algorithm} {stored.get_base64()}\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except Exception:
            raise ControllerError("state_unavailable", 503) from None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)


class _PrerequisiteSession:
    """One authenticated channel and one remote transaction lock, never an RPC."""
    def __init__(self, channel, protocol, password, deadline):
        self.channel, self.protocol = channel, protocol
        self._password, self.deadline = password, deadline

    def receive(self, nonce, *, ready=False):
        from h3server.installer.runner import InstallerBusy
        from h3server.installer.steps import CommandFailure
        output = bytearray()
        marker = (b'H3READY ' if ready else b'\nH3DONE ') + nonce.encode()
        while time.monotonic() < self.deadline.expires_at:
            if self.channel.recv_ready():
                output.extend(self.channel.recv(32768))
            if self.channel.recv_stderr_ready():
                self.channel.recv_stderr(32768)  # never retain remote diagnostics
            if len(output) > 1024 * 1024:
                raise CommandFailure('prerequisite output exceeds limit')
            position = output.find(marker)
            if position >= 0 and output.endswith(b'\n'):
                suffix = bytes(output[position + len(marker):]).strip()
                if ready and not suffix:
                    return ''
                if not ready and suffix.isdigit():
                    if int(suffix):
                        raise CommandFailure('prerequisite command failed (output suppressed)')
                    return bytes(output[:position]).decode('utf-8', errors='replace')
            if self.channel.exit_status_ready() and not self.channel.recv_ready() and not self.channel.recv_stderr_ready():
                if self.channel.recv_exit_status() == 75:
                    raise InstallerBusy('another installer owns the prerequisite transaction')
                raise CommandFailure('prerequisite transaction interrupted; reauthenticate and reinspect')
            time.sleep(min(.005, self.deadline.remaining()))
        raise CommandFailure('prerequisite transaction deadline expired; reauthenticate and reinspect')

    def request(self, operation, arguments=(), password=None):
        nonce = secrets.token_hex(16)
        credential = b'' if password is None else (password + '\n').encode('utf-8')
        frame = f'{operation} {len(arguments)} {len(credential)} {nonce}\n'.encode()
        frame += ''.join(argument + '\n' for argument in arguments).encode() + credential
        pending = memoryview(frame)
        while pending:
            count = self.channel.send(pending)
            if count <= 0:
                raise ControllerError('connection_failed', 502)
            pending = pending[count:]
        return self.receive(nonce)

    def run(self, argv, *, capture=False):
        operation, arguments, password = self.protocol.request(argv, self._password)
        result = self.request(operation, arguments, password)
        return result if capture else ''

    def finish(self):
        self.request('finish')


class SSHConnection:
    """One serialized connection. Passwords are never retained in this object."""

    def __init__(self, state_dir: Path, *, socket_factory=socket.create_connection,
                 transport_factory=paramiko.Transport):
        self.lock = threading.RLock()
        self._store = KnownHosts(state_dir)
        self._socket_factory = socket_factory
        self._transport_factory = transport_factory
        self._socket = None
        self._transport = None
        self._sftp_clients = []
        self._state = "disconnected"
        self._key = None
        self._host_id = None
        self._username = None
        self._challenge_expires = 0.0
        self._local_mode = False

    @_locked
    def connect_local(self):
        if os.name!='posix':raise ControllerError('local_target_unsupported')
        import platform, pwd
        from h3server.controller.local import LocalTransport
        if self._state != 'disconnected':raise ControllerError('already_connected')
        account=pwd.getpwuid(os.getuid())
        self._local_mode=True;self._local_home=account.pw_dir;self._username=account.pw_name
        self._local_platform=platform.system();self._transport=LocalTransport();self._state='authenticated'
        return self.status()

    @_locked
    def connect(self, host: str, port: int, username: str, *, timeout: float = 10) -> dict:
        if not math.isfinite(timeout) or not 0 < timeout <= 10:
            raise ValueError("connection timeout must be in (0, 10]")
        if host=='local' and port==0 and self._local_mode:return self.connect_local()
        expires = time.monotonic() + timeout
        host = _target(host, port, username)
        if self._state != "disconnected":
            raise ControllerError("already_connected")
        self._local_mode=False
        keys = self._store.load()
        try:
            self._socket = _connect_socket(self._socket_factory, (host, port), timeout)
            self._transport = self._transport_factory(self._socket)
            # Remote banners/errors and authentication diagnostics are untrusted
            # and may contain secrets. Do not forward Paramiko wire logs.
            logger_name = "h3server.controller.ssh.private_wire"
            logger = logging.getLogger(logger_name)
            logger.setLevel(logging.CRITICAL + 1)
            logger.propagate = False
            self._transport.set_log_channel(logger_name)
            remaining = max(0.001, expires - time.monotonic())
            self._transport.banner_timeout = remaining
            self._transport.auth_timeout = remaining
            self._transport.channel_timeout = remaining
            with _TransportDeadline(self._socket, self._transport, remaining) as deadline:
                self._transport.start_client(timeout=remaining)
                self._key = self._transport.get_remote_server_key()
            if deadline.expired.is_set() or time.monotonic() >= expires:
                raise ControllerError("connection_failed", 502)
            self._host_id = f"[{host}]:{port}"
            self._username = username
            if keys.lookup(self._host_id) is not None:
                if not keys.check(self._host_id, self._key):
                    raise ControllerError("host_key_changed")
                self._state = "awaiting_authentication"
            else:
                self._state = "awaiting_approval"
                self._challenge_expires = time.monotonic() + 120
            return self.status()
        except ControllerError:
            self.close()
            raise
        except Exception:
            self.close()
            raise ControllerError("connection_failed", 502) from None

    @_locked
    def approve(self, fingerprint: str) -> dict:
        if self._state != "awaiting_approval":
            raise ControllerError("approval_not_pending")
        if time.monotonic() >= self._challenge_expires:
            self.close()
            raise ControllerError("approval_expired")
        if not isinstance(fingerprint, str) or not hmac.compare_digest(fingerprint.encode("utf-8", errors="surrogatepass"), _fingerprint(self._key).encode()):
            raise ControllerError("fingerprint_mismatch")
        self._store.approve(self._host_id, self._key)
        self._state = "awaiting_authentication"
        return self.status()

    @_locked
    def authenticate(self, password: str, *, timeout: float = 10) -> dict:
        if not math.isfinite(timeout) or not 0 < timeout <= 10:
            raise ValueError("authentication timeout must be in (0, 10]")
        if self._state == "awaiting_approval":
            raise ControllerError("trust_required")
        if self._state != "awaiting_authentication":
            raise ControllerError("authentication_not_pending")
        if not isinstance(password, str) or not 0 < len(password) <= 4096 or "\x00" in password:
            raise ControllerError("invalid_credentials", 422)
        failed = False
        with _TransportDeadline(self._socket, self._transport, timeout) as deadline:
            try:
                # The auth request writes before Paramiko's auth_timeout starts.
                # Never fall back to interactive prompts or agent/key search.
                self._transport.auth_password(self._username, password, fallback=False)
                failed = not self._transport.is_authenticated()
            except Exception:
                failed = True
        if failed or deadline.expired.is_set():
            self.close()
            raise ControllerError("authentication_failed", 401) from None
        self._state = "authenticated"
        return self.status()

    @_locked
    def status(self) -> dict:
        if self._transport is not None and not self._transport.is_active():
            self.close()
        result={"state": self._state, "fingerprint": _fingerprint(self._key) if self._key else None,
                "key_type": self._key.get_name() if self._key else None}
        if self._local_mode:result.update(mode='local',local_platform=self._local_platform,local_supported=self._local_platform=='Linux')
        return result

    def _require_authentication(self):
        if self.status()["state"] != "authenticated" or not self._transport.is_authenticated():
            raise ControllerError("authentication_required")

    @_locked
    def close(self) -> dict:
        # Close every owned resource even when another close fails; do not leak
        # transport exception text during error cleanup.
        clients, transport, sock = self._sftp_clients, self._transport, self._socket
        self._sftp_clients = []
        self._transport = self._socket = self._key = self._host_id = self._username = None
        self._state = "disconnected"
        # SFTP/channel close can write: terminate the underlying IO first.
        _abort_transport(sock, transport)
        for client in clients:
            _close_quietly(client)
        return self.status()

    def interrupt_setup(self):
        """Interrupt owned setup IO without waiting for its connection lock.

        Caller must exclude target replacement until its worker has stopped.
        Durable remote package guardians/detached work are not killed or replayed.
        """
        _abort_transport(self._socket, self._transport)

    @_locked
    def open_sftp(self) -> paramiko.SFTPClient:
        """Authenticated SFTP for later controller callers, not an HTTP file API.

        The caller must close its client; disconnect also closes owned clients.
        """
        self._require_authentication()
        if self._local_mode:
            from h3server.controller.local import LocalFiles
            client=LocalFiles(Path(self._local_home)/'MinimaxH3Server')
            self._sftp_clients.append(client)
            return client
        channel = client = None
        failed = False
        with _TransportDeadline(self._socket, self._transport, 10) as deadline:
            try:
                channel = self._transport.open_session(timeout=deadline.remaining())
                channel.settimeout(deadline.remaining())
                channel.invoke_subsystem("sftp")
                client = paramiko.SFTPClient(channel)
            except Exception:
                failed = True
            finally:
                if failed and channel is not None:
                    _close_quietly(channel)
        if deadline.expired.is_set():
            self.close()
            if client is not None:
                _close_quietly(client)
        if failed or deadline.expired.is_set():
            raise ControllerError("sftp_unavailable", 502) from None
        self._sftp_clients.append(client)
        return client

    def _execute(self, command: str, timeout: float, *, stdin: bytes = b"", suppress_output=False) -> CommandResult:
        self._require_authentication()
        channel = None
        output = bytearray()
        errors = bytearray()
        with _TransportDeadline(self._socket, self._transport, timeout) as deadline:
            try:
                channel = self._transport.open_session(timeout=deadline.remaining())
                channel.settimeout(deadline.remaining())
                channel.exec_command(command)
                remaining_input = memoryview(stdin)
                while remaining_input:
                    sent = channel.send(remaining_input)
                    if sent <= 0:
                        raise OSError("channel closed during input")
                    remaining_input = remaining_input[sent:]
                channel.shutdown_write()
                while True:
                    if time.monotonic() >= deadline.expires_at:
                        result = CommandResult(returncode=None, error="timeout")
                        break
                    for ready, receive, buffer in ((channel.recv_ready, channel.recv, output),
                                                   (channel.recv_stderr_ready, channel.recv_stderr, errors)):
                        if ready():
                            buffer.extend(receive(32768))
                    if len(output) + len(errors) > 1024 * 1024:
                        result = CommandResult(returncode=None, error="output-limit")
                        break
                    if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                        # Raw stderr is never retained or returned to HTTP clients.
                        result = CommandResult("" if suppress_output else output.decode("utf-8", errors="strict"), returncode=channel.recv_exit_status())
                        break
                    time.sleep(min(0.005, deadline.remaining()))
            except (TimeoutError, socket.timeout):
                result = CommandResult(returncode=None, error="timeout")
            except Exception:
                result = CommandResult(returncode=None, error="transport-error")
            finally:
                if channel is not None:
                    _close_quietly(channel)
        # Cleanup can be the blocked write. Never report success if the deadline
        # had to abort it; the expired transport is not reusable for another scan.
        if deadline.expired.is_set() or result.error == "timeout":
            self.close()
            return CommandResult(returncode=None, error="timeout")
        return result

    @_locked
    def check_setup_password(self, password: str):
        """Fixed sudo authentication probe; credential/output never leave memory.

        Drain unused input under NOPASSWD before executing the allowlisted true.
        A rejection can also mean sudo policy/requiretty; never retry or prompt.
        """
        if not isinstance(password, str) or not 0 < len(password) <= 4096 or any(c in password for c in '\r\n\x00'):
            raise ControllerError('setup_sudo_password_rejected')
        command = shlex.join(('sudo', '-S', '-k', '-p', '', '--', '/bin/sh', '-c',
                              'cat >/dev/null || exit 76; exec true </dev/null'))
        result = self._execute(command, 30, stdin=(password + '\n').encode(), suppress_output=True)
        if not result.succeeded:
            if result.error:
                raise ControllerError('setup_credential_check_failed')
            raise ControllerError('setup_sudo_password_rejected')

    @_locked
    def install_prerequisites(self, *, root: str, plan: dict, approval,
                              sudo_password: str | None = None) -> str:
        """Python-absent first-run path, composed locally over authenticated SSH.

        Task11 calls this only for approved prerequisite plans. Every command is
        generated by the existing fixed Steps.system_packages algorithm; no
        remote Python or arbitrary shell/script RPC. Sudo receives its credential
        directly on channel stdin, never via echo, shell variables, argv or files.
        A single plan-bound guardian owns flock across staging, all validation,
        mutations and checkpoints. It waits for children before reading EOF; an
        inherited lock supervisor also survives a guardian interruption.
        A dropped channel is NOT success: reauthenticate and rerun checks. Final
        waiting_for_replan marker mandates fresh read-only inspection/approval.
        """
        from h3server.installer.planner import validate_approval
        from h3server.installer.steps import Steps
        self._require_authentication()
        validate_approval(plan, approval)
        if plan['purpose'] != 'prerequisites':
            raise ValueError('only prerequisite discovery plans use this launcher')
        if not _absolute_path(root) or root.endswith('/') or '..' in root.split('/'):
            raise ValueError('invalid application root')
        if sudo_password is not None and (not isinstance(sudo_password, str) or not 0 < len(sudo_password) <= 4096 or any(c in sudo_password for c in '\r\n\x00')):
            raise ValueError('invalid sudo credential')
        with self._prerequisite_transaction(root, plan, approval, sudo_password) as transaction:
            class TargetSteps(Steps):
                def __init__(self):
                    self.plan = plan
                def run(self, *argv, capture=False):
                    return transaction.run(argv, capture=capture)
            TargetSteps().system_packages()
            transaction.finish()
        return 'waiting_for_replan'

    @contextmanager
    def _prerequisite_transaction(self, root, plan, approval, password):
        from h3server.installer.recovery import PrerequisiteProtocol
        from h3server.installer.steps import CommandFailure
        protocol = PrerequisiteProtocol(root, plan, approval)
        channel = None
        with _TransportDeadline(self._socket, self._transport, 3600) as deadline:
            try:
                channel = self._transport.open_session(timeout=min(30, deadline.remaining()))
                channel.settimeout(deadline.remaining())
                nonce = secrets.token_hex(16)
                if self._local_mode:channel.guardian=True
                channel.exec_command(protocol.command(nonce))
                transaction = _PrerequisiteSession(channel, protocol, password, deadline)
                transaction.receive(nonce, ready=True)
                yield transaction
            except (OSError, EOFError, paramiko.SSHException):
                raise CommandFailure('prerequisite channel lost; reauthenticate and reinspect') from None
            finally:
                if channel is not None:
                    # EOF is processed by the guardian only AFTER its current
                    # child/pipeline exits. Closing SSH does not release flock.
                    try:
                        channel.shutdown_write()
                    except Exception:
                        pass
                    _close_quietly(channel)
        if deadline.expired.is_set():
            self.close()
            raise CommandFailure('prerequisite transaction deadline expired')

    @_locked
    def recovery(self, *, root: str, artifact, digest: str, action: str,
                 service_digest: str | None = None, sudo_password: str | None = None,
                 after: int = 0) -> CommandResult:
        """Fixed authenticated target API, not a shell-command escape hatch.

        Controller stages the plan/approval and verified wheel via SFTP. Fetch
        action=service-plan to present the target-root/uid-bound registration
        document BEFORE requesting its separate digest-bound approval.
        prepare/reboot return only detached launch acceptance, never completion;
        poll snapshot for durable state/events. No password in argv/files/output.
        """
        import json
        from h3server.installer.planner import ReleaseArtifact
        if not isinstance(artifact, ReleaseArtifact) or not _absolute_path(root) or root.endswith('/') or '..' in root.split('/'):
            raise ValueError("invalid recovery target/artifact")
        if not re.fullmatch(r"[a-f0-9]{64}", digest) or action not in {"service-plan", "prepare", "reboot", "resume", "snapshot"}:
            raise ValueError("invalid recovery operation")
        if type(after) is not int or after < 0:
            raise ValueError("invalid replay cursor")
        if action == "prepare" and (not isinstance(service_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", service_digest)):
            raise ValueError("service approval digest required")
        if sudo_password is not None and (action not in {"prepare", "reboot"} or not isinstance(sudo_password, str) or not 0 < len(sudo_password) <= 4096 or any(c in sudo_password for c in '\r\n\x00')):
            raise ValueError("invalid sudo credential scope")
        wheel = root + '/releases/' + artifact.release_id + '/' + artifact.filename
        argv = ['env', '-u', 'PYTHONHOME', '-u', 'PYTHONSTARTUP', 'PYTHONNOUSERSITE=1',
                'PYTHONPATH=' + wheel, 'python3', '-m', 'h3server.installer.recovery',
                '--root', root, '--digest', digest]
        if action not in {'snapshot', 'service-plan'}:
            argv.append('--detach')
        if action == 'prepare':
            argv.extend(('--service-digest', service_digest))
        argv.extend((action, '--after', str(after)))
        command = "printf '%s  %s\\n' " + shlex.join((artifact.sha256, wheel)) + " | sha256sum -c - >/dev/null && " + shlex.join(argv)
        data = json.dumps({'sudo_password': sudo_password}).encode() if action in {'prepare', 'reboot'} else b''
        return self._execute(command, 30, stdin=data, suppress_output=action not in {'snapshot', 'service-plan'})

    @contextmanager
    def deployment_deadline(self, timeout=180):
        """Bound staging including Paramiko writes/rekey/cleanup, not only reads."""
        with self.lock:
            self._require_authentication()
            with _TransportDeadline(self._socket, self._transport, timeout) as deadline:
                yield deadline
            if deadline.expired.is_set():
                self.close()
                raise ControllerError('deployment_timeout', 504)

    @contextmanager
    def staging_transaction(self, root, *, discovery=False):
        """Reentrant on this connection; never upgrade an already-owned lock set."""
        with self.lock:
            self._require_authentication()
            owned = getattr(self, '_staging_owned', None)
            if owned is not None:
                if owned[0] != root or (discovery and not owned[1]):
                    raise ControllerError('staging_lock_scope_changed')
                owned[2]()
                yield owned[2]
                return
            from h3server.controller.discovery import directory
            with self.private_sftp() as sftp:
                try:
                    directory(sftp, root + '/state')
                except (OSError, ValueError):
                    raise ControllerError('staging_lock_unavailable') from None
            with self._staging_transaction(root, discovery=discovery) as check:
                self._staging_owned = (root, discovery, check)
                try:
                    check()
                    yield check
                    check()
                finally:
                    self._staging_owned = None

    @contextmanager
    def _staging_transaction(self, root, *, discovery=False):
        from h3server.controller.discovery import staging_command
        if not _absolute_path(root) or '..' in root.split('/'):
            raise ValueError('invalid staging root')
        channel = None
        with _TransportDeadline(self._socket, self._transport, 180) as deadline:
            try:
                channel = self._transport.open_session(timeout=min(30, deadline.remaining()))
                channel.settimeout(deadline.remaining())
                nonce = secrets.token_hex(16)
                if self._local_mode:channel.guardian=True
                channel.exec_command(staging_command(root, discovery=discovery, nonce=nonce))
                session = _PrerequisiteSession(channel, None, None, deadline)
                try:
                    session.receive(nonce, ready=True)
                except Exception:
                    raise ControllerError('staging_lock_unavailable') from None
                def check():
                    if channel.exit_status_ready() or channel.closed or deadline.expired.is_set():
                        raise ControllerError('staging_lock_lost')
                yield check
                # An ended guardian cannot authorize any subsequent staging.
                check()
                channel.sendall(b'finish\n')
                channel.shutdown_write()
                if channel.recv_exit_status() != 0:
                    raise ControllerError('staging_lock_lost')
            except (OSError, EOFError, paramiko.SSHException):
                raise ControllerError('staging_lock_lost') from None
            finally:
                if channel is not None:
                    try:
                        channel.shutdown_write()
                    except Exception:
                        pass
                    _close_quietly(channel)
        if deadline.expired.is_set():
            self.close()
            raise ControllerError('staging_lock_lost')

    @contextmanager
    def private_sftp(self):
        with self.deployment_deadline():
            client = self.open_sftp()
            try:
                yield client
            finally:
                _close_quietly(client)
                if client in self._sftp_clients:
                    self._sftp_clients.remove(client)

    @_locked
    def inspect_install(self, *, sudo_password_available=False):
        """Fixed read-only prerequisite inspector, not an arbitrary HTTP argv API."""
        from h3server.installer.planner import inspect_prerequisites
        return inspect_prerequisites(lambda argv, timeout: self._execute('LC_ALL=C ' + shlex.join(argv), timeout),
                                     sudo_password_available=sudo_password_available)

    @_locked
    def open_api_channel(self):
        self._require_authentication()
        with _TransportDeadline(self._socket, self._transport, 10) as deadline:
            try:
                channel = self._transport.open_channel('direct-tcpip', ('127.0.0.1', 8765), ('127.0.0.1', 0), timeout=deadline.remaining())
                channel.settimeout(30)
            except Exception:
                raise ControllerError('remote_unavailable', 502) from None
        if deadline.expired.is_set():
            self.close()
            raise ControllerError('remote_unavailable', 502)
        return channel

    @_locked
    def deployment_action(self, *, root, artifact, digest, action, data):
        import json
        from h3server.installer.planner import ReleaseArtifact
        if (not isinstance(artifact, ReleaseArtifact) or not _absolute_path(root) or '..' in root.split('/')
                or not re.fullmatch('[a-f0-9]{64}', digest)
                or action not in {'snapshot', 'activate', 'download', 'cancel-download', 'repair', 'install-update', 'retry-stage'}):
            raise ValueError('invalid deployment operation')
        wheel = root + '/releases/' + artifact.release_id + '/' + artifact.filename
        argv = ['env', '-u', 'PYTHONHOME', '-u', 'PYTHONSTARTUP', 'PYTHONNOUSERSITE=1', 'PYTHONPATH=' + wheel,
                'python3', '-m', 'h3server.controller.target', '--root', root, '--digest', digest, action]
        command = "printf '%s  %s\\n' " + shlex.join((artifact.sha256, wheel)) + ' | sha256sum -c - >/dev/null && ' + shlex.join(argv)
        result = self._execute(command, 30, stdin=json.dumps(data).encode())
        if not result.succeeded:
            raise ControllerError('deployment_operation_failed', 502)
        try:
            return json.loads(result.stdout)
        except ValueError:
            raise ControllerError('invalid_remote_response', 502) from None

    @_locked
    def remote_home(self) -> str:
        # Fixed shell syntax: expand exactly HOME once, quote it, and never
        # interpret the returned path as code. No HTTP-supplied shell fragments.
        if self._local_mode:
            self._require_authentication()
            return self._local_home
        result = self._execute("printf '%s' \"$HOME\"", 5)
        if not result.succeeded or not _absolute_path(result.stdout):
            raise ControllerError("invalid_remote_home", 502)
        return result.stdout

    @_locked
    def __call__(self, argv: tuple[str, ...], *, timeout: float) -> CommandResult:
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("timeout must be finite and in (0, 30] seconds")
        if not isinstance(argv, tuple):
            raise ValueError("probe commands must be argv tuples")
        if len(argv) == 4 and argv[:3] == ("df", "-Pk", "--"):
            if not _absolute_path(argv[3]):
                raise ValueError("disk path must be absolute without control characters")
        elif argv not in PROBE_COMMANDS.values():
            raise ValueError("command is not a read-only probe command")
        return self._execute("LC_ALL=C " + shlex.join(argv), timeout)
