"""Trust-before-authentication SSH and bounded, allowlisted read-only commands."""

from base64 import b64encode
from functools import wraps
import hashlib
import hmac
import ipaddress
import logging
import math
import os
from pathlib import Path
import re
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


def _channel_timer(channel, timeout: float) -> threading.Timer:
    # Paramiko's exec/subsystem acknowledgement waits do NOT honor settimeout.
    # Closing the channel signals those waits as well as blocked reads/writes.
    timer = threading.Timer(timeout, _close_quietly, args=(channel,))
    timer.daemon = True
    timer.start()
    return timer


class KnownHosts:
    """Dedicated trust store; never imports account credentials or SSH config.

    POSIX ownership/mode enforcement is implemented here. Windows ACL support
    requires native integration; persistence explicitly fails closed there.
    """

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.path = self.directory / "known_hosts"

    def load(self) -> paramiko.HostKeys:
        try:
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
                for line in stream:
                    if not line.strip() or line.startswith("#"):
                        continue
                    entry = paramiko.hostkeys.HostKeyEntry.from_line(line.strip())
                    if entry is None or entry.key is None:
                        raise ValueError("invalid trust file")
                    for host in entry.hostnames:
                        if keys.lookup(host) is not None and entry.key.get_name() in keys[host]:
                            raise ValueError("duplicate trust entry")
                        keys.add(host, entry.key.get_name(), entry.key)
            return keys
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

    @_locked
    def connect(self, host: str, port: int, username: str) -> dict:
        host = _target(host, port, username)
        if self._state != "disconnected":
            raise ControllerError("already_connected")
        keys = self._store.load()
        try:
            self._socket = self._socket_factory((host, port), timeout=10)
            self._transport = self._transport_factory(self._socket)
            # Remote banners/errors and authentication diagnostics are untrusted
            # and may contain secrets. Do not forward Paramiko wire logs.
            logger_name = "h3server.controller.ssh.private_wire"
            logger = logging.getLogger(logger_name)
            logger.setLevel(logging.CRITICAL + 1)
            logger.propagate = False
            self._transport.set_log_channel(logger_name)
            self._transport.banner_timeout = 10
            self._transport.auth_timeout = 10
            self._transport.channel_timeout = 10
            self._transport.start_client(timeout=10)
            self._key = self._transport.get_remote_server_key()
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
    def authenticate(self, password: str) -> dict:
        if self._state == "awaiting_approval":
            raise ControllerError("trust_required")
        if self._state != "awaiting_authentication":
            raise ControllerError("authentication_not_pending")
        if not isinstance(password, str) or not 0 < len(password) <= 4096 or "\x00" in password:
            raise ControllerError("invalid_credentials", 422)
        try:
            # Never fall back to keyboard-interactive prompts or agent/key search.
            self._transport.auth_password(self._username, password, fallback=False)
            if not self._transport.is_authenticated():
                raise paramiko.AuthenticationException()
            self._state = "authenticated"
            return self.status()
        except Exception:
            self.close()
            raise ControllerError("authentication_failed", 401) from None

    @_locked
    def status(self) -> dict:
        if self._transport is not None and not self._transport.is_active():
            self.close()
        return {"state": self._state, "fingerprint": _fingerprint(self._key) if self._key else None,
                "key_type": self._key.get_name() if self._key else None}

    def _require_authentication(self):
        if self.status()["state"] != "authenticated" or not self._transport.is_authenticated():
            raise ControllerError("authentication_required")

    @_locked
    def close(self) -> dict:
        # Close every owned resource even when another close fails; do not leak
        # transport exception text during error cleanup.
        resources = [*self._sftp_clients, self._transport, self._socket]
        self._sftp_clients = []
        self._transport = self._socket = self._key = self._host_id = self._username = None
        self._state = "disconnected"
        for resource in resources:
            if resource is not None:
                _close_quietly(resource)
        return {"state": "disconnected", "fingerprint": None, "key_type": None}

    @_locked
    def open_sftp(self) -> paramiko.SFTPClient:
        """Authenticated SFTP for later controller callers, not an HTTP file API.

        The caller must close its client; disconnect also closes owned clients.
        """
        self._require_authentication()
        try:
            channel = self._transport.open_session(timeout=10)
            timer = _channel_timer(channel, 10)
            try:
                channel.settimeout(10)
                channel.invoke_subsystem("sftp")
                client = paramiko.SFTPClient(channel)
            except Exception:
                _close_quietly(channel)
                raise
            finally:
                timer.cancel()
            self._sftp_clients.append(client)
            return client
        except Exception:
            raise ControllerError("sftp_unavailable", 502) from None

    def _execute(self, command: str, timeout: float) -> CommandResult:
        self._require_authentication()
        channel = None
        timer = None
        deadline = time.monotonic() + timeout
        output = bytearray()
        errors = bytearray()
        try:
            channel = self._transport.open_session(timeout=timeout)
            remaining = max(0.001, deadline - time.monotonic())
            timer = _channel_timer(channel, remaining)
            channel.settimeout(remaining)
            channel.exec_command(command)
            channel.shutdown_write()
            while True:
                if time.monotonic() >= deadline:
                    return CommandResult(returncode=None, error="timeout")
                for ready, receive, buffer in ((channel.recv_ready, channel.recv, output),
                                               (channel.recv_stderr_ready, channel.recv_stderr, errors)):
                    if ready():
                        buffer.extend(receive(32768))
                if len(output) + len(errors) > 1024 * 1024:
                    return CommandResult(returncode=None, error="output-limit")
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    # Raw stderr is never retained or returned to HTTP clients.
                    return CommandResult(output.decode("utf-8", errors="strict"), returncode=channel.recv_exit_status())
                time.sleep(min(0.005, max(0, deadline - time.monotonic())))
        except (TimeoutError, socket.timeout):
            return CommandResult(returncode=None, error="timeout")
        except Exception:
            error = "timeout" if time.monotonic() >= deadline else "transport-error"
            return CommandResult(returncode=None, error=error)
        finally:
            if timer is not None:
                timer.cancel()
            if channel is not None:
                _close_quietly(channel)

    @_locked
    def remote_home(self) -> str:
        # Fixed shell syntax: expand exactly HOME once, quote it, and never
        # interpret the returned path as code. No HTTP-supplied shell fragments.
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
