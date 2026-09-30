"""Idle-only optional sparse acquisition and GPU validation; no pip/system changes."""
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from h3server.backends import sparse
from h3server.backends.base import atomic_json, read_json, regular_file
from h3server.installer.diagnostics import safe_log

STATE = 'state/sparse-install.json'
LOCK = 'state/sparse-install.lock'


class SparseInstaller:
    def __init__(self, worker):
        self.worker, self.paths = worker, worker.paths
        self.lock = threading.RLock()
        self.thread = None
        self.stop = threading.Event()
        self.last_progress = 0

    def _read(self):
        try: return read_json(self.paths, STATE)
        except FileNotFoundError: return dict(state='not_installed', completed=0, total=sparse.TOTAL, error=None)

    def _publish(self, **values):
        with self.lock: atomic_json(self.paths, STATE, {**self._read(), **values})

    def status(self):
        with self.lock:
            return {**self._read(), 'installed': sparse.installed(self.paths),
                    'busy': bool(self.thread and self.thread.is_alive()),
                    'supported': bool(self.worker.backend.profiles) and any(p in sparse.PROFILES for p in self.worker.backend.profiles),
                    'supported_profiles': sparse.PROFILES, 'artifact_digest': sparse.DIGEST,
                    'total': sparse.TOTAL, 'additional_weights': False, 'turbo_compatible': False}

    def recover(self):
        import fcntl
        with self.lock:
            state = self._read(); maintenance = self.worker.maintenance_status()
            if not state.get('operation') or maintenance.get('operation') != state['operation'] or not maintenance.get('active'): return
            with regular_file(self.paths, LOCK, flags=os.O_CREAT | os.O_RDWR, mode='r+b', create_parents=True) as guard:
                try: fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError: return
                self.worker.end_maintenance(state['operation'])
                if state.get('state') == 'installing': self._publish(state='failed', error='Interrupted sparse installation; retry explicitly to resume.')

    def start(self, artifact_digest):
        if artifact_digest != sparse.DIGEST:
            raise ValueError('Sparse selection changed')
        with self.lock:
            if self.thread and self.thread.is_alive(): return self.status()
            self.recover()
            if not self.status()['supported']: raise ValueError('Sparse currently supports ConvRot INT8 only')
            if sparse.installed(self.paths, verify=True): return self.status()
            from h3server.downloads import require_consent
            require_consent(self.paths, profile_name=self.worker.backend.profiles[0])
            operation = uuid.uuid4().hex
            self.worker.begin_maintenance(operation, policy='reject')
            try:
                atomic_json(self.paths, sparse.CONSENT, sparse.BINDING)
                atomic_json(self.paths, sparse.GPU_RECEIPT, {'gpu_probe': False, 'artifact_digest': sparse.DIGEST})
                self._publish(state='installing', operation=operation, error=None, log='')
                self.stop.clear()
                self.thread = threading.Thread(target=self._run, args=(operation,), daemon=True, name='h3-sparse-install')
                self.thread.start()
            except BaseException:
                self.worker.end_maintenance(operation); raise
            return self.status()

    def progress(self, completed, total):
        now = time.monotonic()
        if now - self.last_progress >= .25 or completed == total:
            self._publish(completed=completed, total=total)
            self.last_progress = now

    def write(self, text):
        self._publish(log=(self._read().get('log', '') + safe_log(text))[-16000:])

    def flush(self): pass

    def _run(self, operation, *, release=True, maintenance_fd=None):
        import fcntl
        from h3server.installer.steps import ProcessIO, environment
        try:
            with regular_file(self.paths, LOCK, flags=os.O_CREAT | os.O_RDWR, mode='r+b', create_parents=True) as guard:
                fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
                sparse.download(self.paths, progress=self.progress, cancelled=self.stop.is_set)
                if self.stop.is_set(): raise InterruptedError('Sparse installation cancelled')
                self.write('Files verified; checking native sparse kernels on this GPU. No dependencies are being changed.\n')
                io = ProcessIO(command_timeout=180)
                io.lock_fd = maintenance_fd if maintenance_fd is not None else guard.fileno()
                io.run((sys.executable, '-c', 'import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_module("h3server.backends.sparse",run_name="__main__")',
                        str(Path(__file__).resolve().parents[2]), str(self.paths.root)), env=environment(self.paths), log=self)
                if not sparse.installed(self.paths, verify=True): raise ValueError('Sparse GPU receipt verification failed')
                self._publish(state='installed', completed=sparse.TOTAL, error=None)
        except Exception as error:
            self._publish(state='failed', error=safe_log(f'{type(error).__name__}: {error}')[:2000])
        finally:
            if release:self.worker.end_maintenance(operation)

    def close(self):
        self.stop.set()
        if self.thread: self.thread.join()
