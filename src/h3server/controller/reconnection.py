"""Bounded, memory-only setup observation recovery; never relaunch remote work.

The operation lock serializes explicit HTTP operations. A recovery worker owns
SSH until joined, so switching targets cannot race an old authentication/restore.
Python strings cannot be reliably zeroized; clearing drops owned references.
"""
from contextlib import contextmanager
import threading
import time

import paramiko

from h3server.controller.ssh import ControllerError
from h3server.installer.recovery import reconnect
from h3server.store import canonical_plan_digest


class SetupRecovery:
    def __init__(self, connection, deployment, session_active, *, timeout=120):
        self.connection, self.deployment = connection, deployment
        self._session_active, self._timeout = session_active, timeout
        self.operations = threading.RLock()
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._worker = None
        self._password = self._target = None
        self._setup_enabled = False
        self.observation_allowed = lambda: True
        self._state, self._error = 'idle', None
        self._cycle = 0

    def status(self):
        with self._lock:
            return {'state': self._state, 'error': self._error, 'cycle': self._cycle,
                    'credential_retained': self._password is not None,
                    'setup_credential_retained': self._password is not None and self._setup_enabled}

    def active(self):
        with self._lock:
            return self._worker is not None and self._worker.is_alive()

    def _publish(self, state, error=None):
        with self._lock:
            self._state, self._error = state, error

    def expire(self):
        # Called periodically even when no browser is open. Never wait on SSH IO.
        if not self._session_active():
            self.revoke()

    def revoke(self):
        """Drop credentials and signal recovery without waiting for remote IO."""
        with self._lock:
            self._password = None
            self._setup_enabled = False
            self._cancel.set()

    def target(self):
        with self._lock:
            return self._target

    def setup_target(self):
        with self._lock:
            if not self._setup_enabled or self._password is None or not self._session_active():
                raise ControllerError('setup_credential_required')
            return self._target

    def setup_password(self, target):
        with self._lock:
            if target != self.setup_target():
                raise ControllerError('recovery_binding_changed')
            return self._password

    def observe(self):
        """Detect setup transport loss even if the browser is not polling."""
        if not self.operations.acquire(blocking=False):
            return
        try:
            if (self.observation_allowed() and not self.active() and self._password is not None
                    and self.deployment.plan is not None
                    and self.connection.status()['state'] == 'disconnected'):
                self.start()
        finally:
            self.operations.release()

    def clear(self):
        with self.operations:
            with self._lock:
                self.revoke()
                worker = self._worker
            if worker is not None:
                worker.join()
            with self._lock:
                self._worker = None
                self._state, self._error = 'idle', None

    def close(self):
        self.clear()
        self._target = None

    def set_target(self, target):
        self.clear()
        self._target = target

    def remember(self, password, *, enabled, reuse_for_setup=False):
        self.clear()
        with self._lock:
            if enabled and self._target is not None and self._session_active():
                self._password = password
                self._setup_enabled = reuse_for_setup

    @contextmanager
    def operation(self, *, replace=False):
        with self.operations:
            if replace:
                self.clear()
            elif self.active():
                raise ControllerError('recovery_in_progress')
            yield

    def start(self):
        with self.operations, self._lock:
            if (self.active() or self._state == 'failed' or self._password is None
                    or self._target is None or not self._session_active()
                    or self.deployment.plan is None or self.deployment.root is None):
                return False
            digest = canonical_plan_digest(self.deployment.plan)
            root = self.deployment.root
            self._cancel = threading.Event()
            self._cycle += 1
            self._state, self._error = 'connecting', None
            self._worker = threading.Thread(target=self._run, args=(self._target, digest, root),
                                            name='h3-setup-recovery', daemon=True)
            self._worker.start()
            return True

    def _run(self, target, digest, root):
        def factory():
            self._publish('connecting')
            self.connection.close()
            return self.connection

        def wait(delay):
            self._publish('backoff')
            return self._cancel.wait(delay)

        deadline = time.monotonic() + self._timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ControllerError('reconnect_timeout')
                reconnect(factory, target, self._password, timeout=remaining,
                          cancel=self._cancel, wait=wait)
                if self._cancel.is_set() or not self._session_active():
                    raise ControllerError('cancelled')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ControllerError('reconnect_timeout')
                try:
                    # Include restore/SFTP/rekey/cleanup in the total budget.
                    # Explicit cancellation waits at most this bounded IO slice.
                    with self.connection.deployment_deadline(timeout=min(10, remaining)):
                        self.deployment.restore(expected_digest=digest, expected_root=root, observation_only=True)
                except (ControllerError, OSError, EOFError, paramiko.SSHException) as error:
                    transient = not isinstance(error, ControllerError) or error.code in {
                        'invalid_remote_home', 'sftp_unavailable', 'service_plan_unavailable',
                        'deployment_timeout', 'authentication_required'}
                    if not transient or self.connection.status()['state'] != 'disconnected':
                        raise
                    if wait(min(.5, max(0, deadline - time.monotonic()))):
                        raise ControllerError('cancelled')
                    continue
                if self._cancel.is_set() or not self._session_active():
                    raise ControllerError('cancelled')
                if time.monotonic() >= deadline:
                    raise ControllerError('reconnect_timeout')
                self._publish('recovered')
                break
        except Exception as error:
            self.connection.close()
            with self._lock:
                self._password = None
            # Never forward arbitrary remote exceptions or diagnostics.
            safe = {'authentication_failed', 'host_key_changed', 'trust_required',
                    'reconnect_timeout', 'cancelled', 'state_unavailable',
                    'recovery_binding_changed', 'matching_release_required', 'invalid_checkpoint'}
            code = error.code if isinstance(error, ControllerError) and error.code in safe else 'recovery_failed'
            self._publish('failed', code)
