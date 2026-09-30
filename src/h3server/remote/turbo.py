"""Explicit optional Turbo acquisition; durable progress, idle-only maintenance."""
import os
import threading
import time
import uuid

from h3server.backends import turbo
from h3server.backends.base import atomic_json, read_json, regular_file
from h3server.installer.diagnostics import safe_log

STATE = 'state/turbo-install.json'
LOCK = 'state/turbo-install.lock'


class TurboInstaller:
    def __init__(self, worker):
        self.worker, self.paths = worker, worker.paths
        self.lock = threading.RLock()
        self.thread = None
        self.stop = threading.Event()
        self.last_progress = 0

    def _read(self):
        try: return read_json(self.paths, STATE)
        except FileNotFoundError: return dict(state='not_installed', completed=0, total=turbo.TOTAL, error=None)

    def _publish(self, **values):
        with self.lock: atomic_json(self.paths, STATE, {**self._read(), **values})

    def status(self):
        with self.lock:
            return {**self._read(), 'installed': turbo.installed(self.paths),
                    'busy': bool(self.thread and self.thread.is_alive()),
                    'supported': any(p in turbo.TURBO_PROFILES for p in self.worker.backend.profiles),
                    'supported_profiles': turbo.TURBO_PROFILES, 'checkpoint': turbo.WEIGHT,
                    'artifact_digest': turbo.DIGEST, 'license': 'Apache-2.0', 'total': turbo.TOTAL}

    def recover(self):
        import fcntl
        with self.lock:
            state = self._read(); maintenance = self.worker.maintenance_status()
            if not state.get('operation') or maintenance.get('operation') != state['operation'] or not maintenance.get('active'): return
            with regular_file(self.paths, LOCK, flags=os.O_CREAT | os.O_RDWR, mode='r+b', create_parents=True) as guard:
                try: fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError: return
                self.worker.end_maintenance(state['operation'])
                if state.get('state') == 'installing': self._publish(state='failed', error='Interrupted Turbo download; retry explicitly to resume.')

    def start(self, artifact_digest):
        if artifact_digest != turbo.DIGEST:
            raise ValueError('Turbo selection changed')
        with self.lock:
            if self.thread and self.thread.is_alive(): return self.status()
            self.recover()
            if not self.status()['supported']: raise ValueError('Turbo currently supports ConvRot INT8 and mixed profiles only')
            if turbo.installed(self.paths, verify=True): return self.status()
            from h3server.downloads import require_consent
            require_consent(self.paths, profile_name=self.worker.backend.profiles[0])
            operation = uuid.uuid4().hex
            self.worker.begin_maintenance(operation, policy='reject')
            try:
                atomic_json(self.paths, turbo.CONSENT, turbo.BINDING)
                self._publish(state='installing', operation=operation, error=None)
                self.stop.clear()
                self.thread = threading.Thread(target=self._run, args=(operation,), daemon=True, name='h3-turbo-download')
                self.thread.start()
            except BaseException:
                self.worker.end_maintenance(operation); raise
            return self.status()

    def progress(self, completed, total):
        now = time.monotonic()
        if now - self.last_progress >= .25 or completed == total:
            self._publish(completed=completed, total=total)
            self.last_progress = now

    def _run(self, operation, *, release=True, maintenance_fd=None):
        import fcntl
        try:
            with regular_file(self.paths, LOCK, flags=os.O_CREAT | os.O_RDWR, mode='r+b', create_parents=True) as guard:
                fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
                turbo.download(self.paths, progress=self.progress, cancelled=self.stop.is_set)
                self._publish(state='installed', completed=turbo.TOTAL, error=None)
        except Exception as error:
            self._publish(state='failed', error=safe_log(f'{type(error).__name__}: {error}')[:2000])
        finally:
            if release:self.worker.end_maintenance(operation)

    def close(self):
        self.stop.set()
        if self.thread: self.thread.join()
