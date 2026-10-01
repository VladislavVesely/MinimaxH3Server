"""Explicit, bounded optional installation under the daemon maintenance gate."""
from importlib import metadata
import os
from pathlib import Path
import sys
import threading
import uuid

from h3server.backends.base import atomic_json, read_json, regular_file
from h3server.backends.catalog import PRUNED_PROFILE_NAMES
from h3server.backends.sage_attention import VERSION, WHEEL_SHA256, RECEIPT, installed, receipt_path
from h3server.installer.diagnostics import safe_log
from h3server.installer.steps import ProcessIO, environment

STATE = 'state/sageattention-install.json'


class SageInstaller:
    def __init__(self, worker):
        self.worker, self.paths = worker, worker.paths
        self.lock = threading.RLock()
        self.thread = None

    def _read(self):
        try:
            return read_json(self.paths, STATE)
        except FileNotFoundError:
            return {'state': 'not_installed', 'log': '', 'error': None}

    def status(self):
        with self.lock:
            state = self._read()
            verified = installed(self.paths)
            return {**state, 'installed': verified, 'version': VERSION,
                    'supported': any(p in PRUNED_PROFILE_NAMES for p in self.worker.backend.profiles),
                    'busy': self.thread is not None and self.thread.is_alive()}

    def _publish(self, **values):
        with self.lock:
            atomic_json(self.paths, STATE, {**self._read(), **values})

    def recover(self):
        """Release only our own interrupted maintenance after proving no installer lives."""
        import fcntl
        with self.lock:
            state = self._read()
            maintenance = self.worker.maintenance_status()
            if not state.get('operation') or not maintenance.get('active') or maintenance.get('operation') != state['operation']:
                return
            with regular_file(self.paths, 'state/sageattention.lock', flags=os.O_CREAT | os.O_RDWR,
                              mode='r+b', create_parents=True) as guard:
                try:
                    fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return
                self.worker.end_maintenance(state['operation'])
                if state['state'] == 'installing':
                    self._publish(state='failed', error='Optional installation was interrupted; retry explicitly.')

    def start(self):
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return self.status()
            self.recover()
            if not self.status()['supported']:
                raise ValueError('SageAttention is available only for the ComfyUI pruned profiles')
            if installed(self.paths):
                self._publish(state='installed', error=None)
                return self.status()
            operation = uuid.uuid4().hex
            self.worker.begin_maintenance(operation, policy='reject')
            try:
                self._publish(state='installing', operation=operation, log='', error=None)
                self.thread = threading.Thread(target=self._run, args=(operation,), daemon=True, name='h3-sage-install')
                self.thread.start()
            except BaseException:
                self.worker.end_maintenance(operation)
                raise
            return self.status()

    def _run(self, operation, *, release=True, maintenance_fd=None):
        import fcntl
        try:
            with regular_file(self.paths, 'state/sageattention.lock', flags=os.O_CREAT | os.O_RDWR,
                              mode='r+b', create_parents=True) as guard:
                fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    existing = metadata.version('sageattention')
                except metadata.PackageNotFoundError:
                    existing = None
                if existing not in (None, VERSION):
                    raise RuntimeError('A different SageAttention version is installed; refusing to replace it silently')
                from h3server.backends.comfy_runtime import verify_provisioned
                identifier=Path(sys.prefix).name
                profile = next(p for p in self.worker.backend.profiles if p in PRUNED_PROFILE_NAMES)
                if len(identifier)==64 and all(c in '0123456789abcdef' for c in identifier):
                    try:profile=read_json(self.paths,'environments/'+identifier+'/runtime.json')['profile']
                    except FileNotFoundError:pass
                prefix = verify_provisioned(self.paths, profile, allow_unverified_sage=True)
                addon_only = f'sageattention=={VERSION}' not in (prefix / 'resolved.txt').read_text().splitlines()
                relative = 'tmp/sageattention-' + operation + '.txt'
                with regular_file(self.paths, relative, flags=os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                                  mode='wb', create_parents=True) as requirements:
                    requirements.write(f'sageattention=={VERSION} --hash=sha256:{WHEEL_SHA256}\n'.encode())
                attempted = False
                try:
                    io = ProcessIO(command_timeout=300)
                    io.lock_fd = maintenance_fd if maintenance_fd is not None else guard.fileno()
                    env = environment(self.paths)
                    self.write('Installing pinned optional SageAttention 1.0.6 (pure Triton); Torch dependencies will not be changed.\n')
                    attempted = True
                    io.run((sys.executable, '-m', 'pip', 'install', '--index-url', 'https://pypi.org/simple',
                            '--no-deps', '--force-reinstall', '--only-binary=:all:', '--require-hashes',
                            '-r', str(self.paths.root / relative)), env=env, log=self)
                    io.run((sys.executable, '-m', 'h3server.backends.sage_attention'), env=env, log=self)
                    atomic_json(self.paths, receipt_path(self.paths), {'version': VERSION, 'wheel_sha256': WHEEL_SHA256,
                        'environment': str(Path(sys.prefix).resolve()), 'torch': metadata.version('torch'), 'gpu_probe': True})
                    verify_provisioned(self.paths, profile)
                    self._publish(state='installed', error=None)
                except Exception as error:
                    if attempted and addon_only:
                        try:
                            self.write('Optional component failed; restoring the unchanged base dependency set.\n')
                            io.run((sys.executable, '-m', 'pip', 'uninstall', '--yes', 'sageattention'), env=env, log=self)
                            verify_provisioned(self.paths, profile)
                        except Exception as cleanup:
                            raise RuntimeError(f'{error}; optional package cleanup failed: {cleanup}') from error
                    raise
                finally:
                    (self.paths.root / relative).unlink(missing_ok=True)
        except Exception as error:
            self._publish(state='failed', error=safe_log(f'{type(error).__name__}: {error}')[:4000])
        finally:
            if release:self.worker.end_maintenance(operation)

    def write(self, text):
        with self.lock:
            log = (self._read().get('log', '') + safe_log(text))[-32000:]
            self._publish(log=log)

    def flush(self):
        pass

    def close(self):
        if self.thread is not None:
            self.thread.join()
