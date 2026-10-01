"""Single-process scheduler and isolated, bounded JSONL backend protocol.

Adapter contract: ``python -m h3server.backends.h3 --request PATH`` (legacy),
or ``python -m h3server.backends.pruned --profile NAME --request PATH``.
An approved active profile selects one backend; requests never switch runtimes.
PATH is a daemon-created 0600 JSON document with schema_version=1, job_id,
request (prompt, loras, sparse_attention, normalized settings), output_path and
root. The adapter must use local verified artifacts, enforce persisted license
consent, write ONLY output_path, and emit domain Event JSON lines on stdout.
Allowed stages/units are below. Bounded messages are sanitized and known prompts
redacted before publication. Pruned workers frame Python logs and concrete errors
as these events. Unframed native stderr is discarded. Exit zero is necessary but not sufficient: the daemon checks
the bounded regular output and independently validates its MP4 video stream.

Backend injection is a Python composition seam for the next adapter and explicit
subprocess tests, never a CLI/environment fake-production switch. Readiness must
check the actual environment, consent and artifact integrity, not source support.
The default H3 adapter supplies asynchronous diagnostics and strict profile/LoRA
validation; injected backends may supply the same validate_request contract.

The daemon's POSIX ownership lock is inherited by the child. A crashed daemon
cannot reconcile/start another worker while an orphan retains ownership; the
service manager must stop its entire control group before restart. No stale PID
is trusted or killed. Native Linux service lifecycle acceptance remains required.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import Callable

from h3server.domain import Capabilities, Event, JobRequest, JobStatus, StageStatus, H3_PROFILES
from h3server.events import decode_event
from h3server.paths import AppPaths
from h3server.store import OutputMetadata, QueueFull, Store

MAX_LINE_BYTES = 16384
MAX_STREAM_BYTES = 16 * 1024 * 1024
MAX_WORKER_EVENTS = 10000
STAGES = frozenset({"loading", "text_encoding", "denoising", "video_decoding", "audio_decoding", "export", "mux", "validation"})
UNITS = frozenset({"bytes", "items", "steps", "frames", "chunks"})
TERMINAL = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED})


class BackendUnavailable(RuntimeError):
    pass


def unavailable() -> tuple[str, ...]:
    return ("H3 execution adapter and verified environment/artifact checks are not installed",)


def validate_mp4(fd: int) -> bool:
    """ffprobe validation on the already-confined descriptor, never a reopened path.

    The adapter may instead supply a PyAV validator with this same fd->bool
    contract. It must parse/decode video, not merely trust a filename/header.
    """
    executable = shutil.which("ffprobe")
    if executable is None:
        return False
    path = f"/proc/self/fd/{fd}" if sys.platform.startswith("linux") else f"/dev/fd/{fd}"
    try:
        result = subprocess.run(
            [executable, "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
             "format=format_name,duration:stream=codec_type,width,height,nb_read_frames", "-of", "json", path],
            pass_fds=(fd,), capture_output=True, timeout=120, check=False,
        )
        if result.returncode or result.stderr or len(result.stdout) > MAX_LINE_BYTES:
            return False
        data = json.loads(result.stdout)
        streams = data["streams"]
        duration = float(data["format"]["duration"])
        return bool("mp4" in data["format"]["format_name"].split(",")
                    and math.isfinite(duration) and duration > 0 and streams
                    and streams[0]["codec_type"] == "video"
                    and int(streams[0]["width"]) > 0 and int(streams[0]["height"]) > 0
                    and int(streams[0]["nb_read_frames"]) > 0)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return False


@dataclass(frozen=True)
class Backend:
    command: tuple[str, ...] = (sys.executable, "-m", "h3server.backends.h3")
    readiness: Callable[[], tuple[str, ...]] = unavailable
    capabilities: Capabilities = field(default_factory=Capabilities)
    validate_video: Callable[[int], bool] = validate_mp4
    validate_request: Callable[[JobRequest], None] = lambda request: None
    diagnostics: Callable[[], dict] = lambda: {}
    runtime_id: str = 'diffusers-minimax-h3-t2va'
    profiles: tuple[str, ...] = H3_PROFILES
    resident: bool = False
    command_for_profile: Callable[[str], tuple[str, ...]] | None = None
    backend_for_profile: Callable[[str], "Backend"] | None = None


def _job_id(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("invalid job identifier")
    return value


@contextmanager
def _directory(root: Path, *parts: str):
    """Walk with no-follow directory descriptors, including the configured root."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(root, flags)
    try:
        for part in parts:
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


class Worker:
    def __init__(self, store: Store, paths: AppPaths, *, backend: Backend | None = None,
                 queue_limit: int = 8, max_output_bytes: int = 4 * 1024**3,
                 min_free_bytes: int = 1024**3, retention_seconds: float = 7 * 86400,
                 retained_bytes: int = 20 * 1024**3, max_runtime_seconds: float = 6 * 3600,
                 profile: str | None = None):
        for value in (queue_limit, max_output_bytes, retained_bytes):
            if type(value) is not int or value < 1:
                raise ValueError("worker limits must be positive integers")
        if type(min_free_bytes) is not int or min_free_bytes < 0:
            raise ValueError("free-space reserve must be nonnegative")
        for value in (retention_seconds, max_runtime_seconds):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("worker durations must be positive and finite")
        self.store, self.paths = store, paths
        if backend is None:
            from h3server.backends import select_backend
            backend = select_backend(paths, profile=profile)
        self.backend = backend
        self.queue_limit, self.max_output_bytes = queue_limit, max_output_bytes
        self.min_free_bytes, self.retention_seconds = min_free_bytes, retention_seconds
        self.retained_bytes, self.max_runtime_seconds = retained_bytes, max_runtime_seconds
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread = None
        self._owner_fd = None
        self._process = None
        self._terminated_process = None
        self._active = None
        self._resident_command = None
        self._resident_process = None
        self._resident_components = []
        self._resident_profile = None
        self._reuse_mode = 'warm'
        self._resident_input_mode = None
        from h3server.performance import PerformanceStore
        self.performance = PerformanceStore(paths.state / 'performance.sqlite', root=paths.root)
        self._hardware = None
        self._job_estimates = {}

    def estimate(self, request):
        from h3server.performance import configuration
        with self._lock:
            if request.settings is None:
                return {'samples': 0, 'reason': 'No normalized settings recorded.'}
            mode = self._reuse_mode if self.models_status()['loaded'] and self._resident_profile == request.settings.profile else 'cold'
            if mode != 'cold' and (request.settings.input_mode != 'text' or self._resident_input_mode == 'references'):
                mode = 'encoder_reuse'
            if self._hardware is None:
                return {'samples': 0, 'cache': mode, 'reason': 'No hardware-verified sample in this server session yet.'}
            try:
                return self.performance.estimate(configuration(request, self.paths), self._hardware, mode)
            except Exception:
                return {'samples': 0, 'cache': mode, 'reason': 'Timing statistics are unavailable; generation can still proceed.'}

    def job_estimate(self, job):
        with self._lock:
            if job.status is JobStatus.RUNNING:
                return self._job_estimates.get(job.job_id, {'samples': 0, 'reason': 'No estimate captured for this run.'})
            if job.status is JobStatus.QUEUED: return self.estimate(job.request)
            return None

    @staticmethod
    def _alive(process):
        return process is not None and process.returncode is None and os.waitid(
            os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None

    def _drop_resident(self):
        process = self._resident_process
        if process is not None:
            self._terminate(process)
            for stream in (process.stdin, process.stdout):
                if stream is not None: stream.close()
        self._resident_process = None
        self._resident_components = []
        self._resident_profile = None
        self._reuse_mode = 'warm'
        self._resident_input_mode = None

    def models_status(self):
        with self._lock:
            state = self.maintenance_status()
            loaded = self._alive(self._resident_process) and bool(self._resident_components)
            return {'supported': self.backend.resident, 'loaded': loaded, 'observation': 'last_completed_run',
                    'components': list(self._resident_components) if loaded else [],
                    'profile': self._resident_profile if loaded else None,
                    'busy': not state['idle'], 'maintenance': bool(state['active']),
                    'can_unload': loaded and state['idle'] and not state['active'],
                    'placement': 'GPU/CPU placement is managed automatically; residency is not a VRAM guarantee.'}

    def unload_models(self):
        with self._lock:
            state = self.maintenance_status()
            if not state['idle'] or state['active']:
                raise ValueError('models busy: queued/running work or maintenance')
            self._drop_resident()
            return self.models_status()

    def selected_backend(self, profile):
        backend = self.backend.backend_for_profile(profile) if self.backend.backend_for_profile else self.backend
        if not isinstance(backend, Backend):
            raise ValueError('Invalid selected runtime backend')
        if self.backend.backend_for_profile and profile not in backend.profiles:
            raise ValueError('Selected runtime does not provide the requested profile')
        return backend

    def readiness(self, profile=None) -> tuple[str, ...]:
        if self.maintenance_status()['active']:
            return ('Installation maintenance is active',)
        if self._stopping.is_set() or (self._thread is not None and not self._thread.is_alive()):
            return ("Worker scheduler is not available",)
        try:
            from h3server.backends.h3 import RuntimeReadiness
            backend = self.selected_backend(profile) if profile is not None else self.backend
            if isinstance(backend.readiness, RuntimeReadiness):
                reasons = backend.readiness(refresh=self._active is None and not self._alive(self._resident_process))
            else:
                reasons = backend.readiness()
            if not isinstance(reasons, tuple) or any(not isinstance(item, str) for item in reasons):
                raise ValueError("invalid readiness result")
            return reasons
        except Exception:
            return ("Backend readiness check failed",)

    def _disk_check(self):
        if shutil.disk_usage(self.paths.root).free < self.min_free_bytes + self.max_output_bytes:
            raise OSError("insufficient output disk reserve")

    def _pending_jobs(self):
        offset = 0
        pending = []
        while rows := self.store.list_jobs(limit=1000, offset=offset):
            pending.extend(job for job in rows if job.status in {JobStatus.QUEUED, JobStatus.RUNNING})
            offset += len(rows)
        return pending

    def maintenance_status(self):
        """Durable fail-closed gate. Only its deployment owner can release it.

        Drain keeps scheduling accepted work; begin and submit share _lock. No
        expiry or daemon restart can reopen submissions during a release switch.
        """
        from h3server.backends.base import read_json
        with self._lock:
            try:
                record = read_json(self.paths, 'state/maintenance.json')
            except FileNotFoundError:
                record = {'operation': None, 'active': False}
            pending = len(self._pending_jobs())
            return {**record, 'pending': pending, 'idle': pending == 0 and self._active is None}

    def begin_maintenance(self, operation, *, policy, cancel_confirmed=False):
        from h3server.backends.base import atomic_json
        if not re.fullmatch('[a-f0-9]{32}', operation) or policy not in {'reject', 'drain', 'cancel'}:
            raise ValueError('invalid maintenance request')
        if policy == 'cancel' and cancel_confirmed is not True:
            raise ValueError('separate cancellation consent required')
        with self._lock:
            state = self.maintenance_status()
            if state['active'] and state['operation'] != operation:
                raise ValueError('maintenance owned by another deployment')
            if policy == 'reject' and not state['idle']:
                raise ValueError('jobs are busy')
            atomic_json(self.paths, 'state/maintenance.json', {'operation': operation, 'active': True})
            if policy == 'cancel':
                for job in self._pending_jobs():
                    self.cancel(job.job_id)
            return self.maintenance_status()

    def end_maintenance(self, operation):
        from h3server.backends.base import atomic_json
        with self._lock:
            state = self.maintenance_status()
            if state['operation'] not in (None, operation):
                raise ValueError('maintenance owner mismatch')
            atomic_json(self.paths, 'state/maintenance.json', {'operation': operation, 'active': False})
            return self.maintenance_status()

    def submit(self, request: JobRequest):
        if request.settings is None:
            raise ValueError("normalized generation settings are required")
        with self._lock:
            backend = self.selected_backend(request.settings.profile)
            request.validate_capabilities(backend.capabilities)
            backend.validate_request(request)
            if self.maintenance_status()['active'] or self._stopping.is_set() or self.readiness(request.settings.profile):
                raise BackendUnavailable("backend is not ready")
            self.cleanup_outputs()
            self._disk_check()
            job = self.store.accept_job(request, capabilities=backend.capabilities, pending_limit=self.queue_limit)
            self._wake.set()
            return job

    def start(self):
        import fcntl
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("worker already started")
            with _directory(self.paths.root, "state") as state_fd:
                fd = os.open("remote.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=state_fd)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise RuntimeError("invalid ownership lock")
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (OSError, RuntimeError):
                os.close(fd)
                raise RuntimeError("remote worker ownership unavailable") from None
            self._owner_fd = fd
            try:
                for job_id in self.store.reconcile_interrupted_jobs():
                    self._cleanup_partial(job_id)
                self._stopping.clear()
                self.cleanup_outputs()
                self._thread = threading.Thread(target=self._schedule, name="h3-worker", daemon=True)
                self._thread.start()
            except BaseException:
                os.close(fd)
                self._owner_fd = None
                raise

    def stop(self):
        with self._lock:
            self._stopping.set()
            self._wake.set()
            if self._active is not None:
                self._terminate(self._process)
                job = self.store.get_job(self._active)
                if job.status is JobStatus.RUNNING:
                    self.store.transition_job(job.job_id, JobStatus.INTERRUPTED)
                if job.status is not JobStatus.COMPLETED:
                    self._cleanup_partial(job.job_id)
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        with self._lock:
            self._drop_resident()
        if self._owner_fd is not None:
            os.close(self._owner_fd)
            self._owner_fd = None

    @staticmethod
    def _wait_exit(process, timeout):
        # Leave the leader waitable until group signalling finishes. Reaping it
        # earlier permits PID/PGID reuse, especially during slow MP4 validation.
        deadline = time.monotonic() + timeout
        while True:
            result = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if result is not None:
                return result
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(process.args, timeout)
            time.sleep(.01)

    def _terminate(self, process):
        with self._lock:
            if process is None or process is self._terminated_process:
                return
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    # Some POSIX kernels report EPERM for an exited group with
                    # no signalable members. Only accept it for our unreaped child.
                    self._wait_exit(process, 0)
                if sig == signal.SIGTERM:
                    try:
                        self._wait_exit(process, 1)
                    except subprocess.TimeoutExpired:
                        pass
            process.wait()
            self._terminated_process = process

    def delete(self, job_id: str):
        import fcntl
        import shutil
        from h3server.backends.base import directory, regular_file
        job_id=_job_id(job_id)
        with self._lock:
            if self._active==job_id:
                raise ValueError('Wait until this job has fully stopped before deleting it')
            if self.maintenance_status()['active']:
                raise ValueError('Wait for maintenance to finish before deleting jobs')
            with regular_file(self.paths,'state/assets.lock',flags=os.O_CREAT|os.O_RDWR,mode='r+b',create_parents=True) as guard:
                fcntl.flock(guard,fcntl.LOCK_EX)
                def cleanup(assets):
                    # dir_fd rmtree never follows nested symlinks. Refuse unsafe
                    # platforms rather than fall back to path-based recursion.
                    if not shutil.rmtree.avoids_symlink_attacks:
                        raise ValueError('Safe directory deletion is unavailable on this platform')
                    with directory(self.paths,'tmp/jobs',create=True) as parent:
                        try:shutil.rmtree(job_id,dir_fd=parent)
                        except FileNotFoundError:pass
                    with directory(self.paths,'assets',create=True) as parent:
                        for asset in assets:
                            for suffix in ('.bin','.json'):
                                try:os.unlink(asset+suffix,dir_fd=parent)
                                except FileNotFoundError:pass
                    self.performance.delete_job(job_id)
                from h3server.characters import CharacterStore
                protected=CharacterStore(self.paths).protected_assets_locked()
                removed=self.store.delete_job(job_id,cleanup,protected_assets=protected)
                return {'deleted':job_id,'removed_assets':removed}

    def cancel(self, job_id: str):
        _job_id(job_id)
        with self._lock:
            job = self.store.get_job(job_id)
            if job.status in TERMINAL and job.status is not JobStatus.INTERRUPTED:
                return job
            if self._active == job_id:
                self._terminate(self._process)
            job = self.store.transition_job(job_id, JobStatus.CANCELLED)
            self._cleanup_partial(job_id)
            self._wake.set()
            return job

    def _schedule(self):
        try:
            self._schedule_jobs()
        except Exception:
            # Persistence/filesystem failures stop acceptance; no raw background
            # exception is printed and no job is fabricated as successful.
            self._stopping.set()

    def _schedule_jobs(self):
        next_cleanup = 0.0
        while not self._stopping.is_set():
            with self._lock:
                if time.monotonic() >= next_cleanup:
                    self.cleanup_outputs()
                    next_cleanup = time.monotonic() + min(60, self.retention_seconds)
                profile = None
                if self.backend.backend_for_profile:
                    queued = [item for item in self._pending_jobs() if item.status is JobStatus.QUEUED]
                    # Store pages are newest-first; preserve FIFO even when the
                    # next model uses a different environment from the daemon.
                    if queued:
                        profile = queued[-1].request.settings.profile
                job = None if self._stopping.is_set() or self.readiness(profile) else self.store.claim_next_job()
                if job is not None:
                    self._active = job.job_id
            if job is None:
                self._wake.wait(.25)
                self._wake.clear()
                continue
            self._execute(job)

    def _cleanup_partial(self, job_id: str):
        try:
            with _directory(self.paths.root, "tmp", "jobs", _job_id(job_id)) as directory:
                for name in ("request.json", "video.mp4", "partial.mp4"):
                    try:
                        os.unlink(name, dir_fd=directory)
                    except FileNotFoundError:
                        pass
        except (OSError, ValueError):
            # Never follow an attacker-controlled directory to clean it.
            pass

    def _prepare(self, job):
        if job.request.settings is None:
            raise ValueError("legacy job has no executable settings")
        with _directory(self.paths.root, "tmp", "jobs") as base:
            os.mkdir(_job_id(job.job_id), 0o700, dir_fd=base)
        directory_path = self.paths.job_dir(job.job_id)
        document = {"schema_version": 1, "job_id": job.job_id, "root": str(self.paths.root),
                    "request": asdict(job.request), "output_path": str(directory_path / "video.mp4")}
        with _directory(self.paths.root, "tmp", "jobs", job.job_id) as directory:
            fd = os.open("request.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            with os.fdopen(fd, "w") as stream:
                json.dump(document, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
        return directory_path / "request.json"

    def _environment(self):
        # Deliberately do not inherit API/Hub tokens, passwords or arbitrary hooks.
        names = ("PATH", "LANG", "LC_ALL", "CUDA_VISIBLE_DEVICES", "LD_LIBRARY_PATH")
        result = {key: os.environ[key] for key in names if key in os.environ}
        result.update(self.paths.cache_environment())
        result.update({"HOME": str(self.paths.root), "PYTHONUNBUFFERED": "1", "PYTHONNOUSERSITE": "1",
                       "PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
        return result

    def _execute(self, job):
        backend = self.backend
        process = None
        keep_resident = False
        report = None
        run_started = time.monotonic()
        try:
            with self._lock:
                self._job_estimates[job.job_id] = self.estimate(job.request)
                if self.store.get_job(job.job_id).status is not JobStatus.RUNNING:
                    return
                # A queued request may outlive the adapter that accepted it.
                backend = self.selected_backend(job.request.settings.profile)
                try:
                    job.request.validate_capabilities(backend.capabilities)
                except ValueError:
                    raise ValueError('Queued LoRA/attention features are no longer supported by the active backend; check the profile and options') from None
                backend.validate_request(job.request)
                self._disk_check()
                request_path = self._prepare(job)
                command = backend.command_for_profile(job.request.settings.profile) if backend.command_for_profile else backend.command
                if backend.resident:
                    if not self._alive(self._resident_process) or self._resident_profile != job.request.settings.profile or self._resident_command != command:
                        self._drop_resident()
                        self._resident_process = subprocess.Popen(
                            [*command, '--resident'], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=self._environment(),
                            cwd=self.paths.root, start_new_session=True,
                            pass_fds=(self._owner_fd,) if self._owner_fd is not None else ())
                        self._resident_profile = job.request.settings.profile
                        self._resident_command = command
                    process = self._resident_process
                    process.stdin.write((json.dumps({'job_id': job.job_id, 'request_path': str(request_path)})+'\n').encode())
                    process.stdin.flush()
                else:
                    self._drop_resident()
                    process = subprocess.Popen(
                        [*command, '--request', str(request_path)],
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        env=self._environment(), cwd=self.paths.root, start_new_session=True,
                        pass_fds=(self._owner_fd,) if self._owner_fd is not None else ())
                self._process = process
            report = self._consume(process, job.job_id, resident=backend.resident)
            with self._lock:
                if self.store.get_job(job.job_id).status is not JobStatus.RUNNING:
                    return
                if backend.resident:
                    if report is None or report['ok'] is not True or not self._alive(process):
                        exited = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                        detail = '' if exited is None else f' (exit/signal {exited.si_status}; check system/GPU memory if killed)'
                        raise RuntimeError('Resident backend failed; models will be released for safe recovery' + detail)
                else:
                    result = self._wait_exit(process, 5)
                    self._terminate(process)
                    if result.si_code != os.CLD_EXITED or result.si_status != 0:
                        detail = (f'exit code {result.si_status}' if result.si_code == os.CLD_EXITED
                                  else f'signal {result.si_status} (check GPU/system memory and kernel logs)')
                        raise RuntimeError('Backend process terminated: ' + detail)
                fd = self._open_output(job.job_id)
            # Parsing may be slow. Cancellation can still acquire the scheduler
            # lock, kill the process group and revoke/delete the partial output.
            try:
                info = os.fstat(fd)
                if not backend.validate_video(fd):
                    raise ValueError("invalid MP4 output")
                os.fsync(fd)
                with self._lock:
                    if self.store.get_job(job.job_id).status is not JobStatus.RUNNING:
                        return
                    current = self._open_output(job.job_id)
                    try:
                        now = os.fstat(current)
                        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns):
                            raise ValueError("output changed during validation")
                    finally:
                        os.close(current)
                    with _directory(self.paths.root, "tmp", "jobs", job.job_id) as directory:
                        os.unlink("request.json", dir_fd=directory)
                    self.store.transition_job(job.job_id, JobStatus.COMPLETED,
                                              output=OutputMetadata("video.mp4", info.st_size, "video/mp4"))
                    if backend.resident:
                        self._resident_components = report['components']
                        keep_resident = True
                        measurement = report['measurement']
                        if measurement is not None:
                            from h3server.performance import configuration
                            self._hardware = measurement['hardware']
                            self._reuse_mode = measurement['cache'] if measurement['cache'] != 'cold' else 'warm'
                            self._resident_input_mode = job.request.settings.input_mode
                            measurement['metrics']['total_seconds'] = round(time.monotonic() - run_started, 6)
                            try:
                                self.performance.record(job.job_id, configuration(job.request, self.paths), self._hardware,
                                    measurement['metrics'], measurement['cache'], measurement['actual_attention'])
                            except Exception:
                                self.store.append_event('job', job.job_id, Event('performance', StageStatus.FAILED,
                                    'Video completed, but timing statistics could not be saved.'))
            finally:
                os.close(fd)
        except Exception as error:
            from h3server.installer.diagnostics import safe_log
            message = safe_log(f'{type(error).__name__}: {error}'.replace(job.request.prompt, '[prompt redacted]'))[:4000]
            with self._lock:
                if self.store.get_job(job.job_id).status is JobStatus.RUNNING:
                    self.store.append_event('job', job.job_id, Event('worker', StageStatus.FAILED, message))
                    self.store.transition_job(job.job_id, JobStatus.FAILED)
        finally:
            with self._lock:
                if not keep_resident:
                    try:
                        self._terminate(process)
                    finally:
                        if process is not None and process.stdout is not None:
                            process.stdout.close()
                    if backend.resident:
                        self._drop_resident()
                if self.store.get_job(job.job_id).status is not JobStatus.COMPLETED:
                    self._cleanup_partial(job.job_id)
                self._active = self._process = None
                self._job_estimates.pop(job.job_id, None)

    def _consume(self, process, job_id, *, resident=None):
        resident = self.backend.resident if resident is None else resident
        buffer = b""
        total = count = 0
        deadline = time.monotonic() + self.max_runtime_seconds
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                if time.monotonic() > deadline:
                    raise ValueError("worker time limit")
                with self._lock:
                    if self.store.get_job(job_id).status is not JobStatus.RUNNING:
                        return
                if shutil.disk_usage(self.paths.root).free < self.min_free_bytes:
                    raise OSError("disk reserve exhausted")
                try:
                    fd = self._open_output(job_id, allow_empty=True)
                    os.close(fd)
                except FileNotFoundError:
                    pass
                if not selector.select(.1):
                    continue
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    if buffer:
                        raise ValueError("unterminated worker event")
                    return
                total += len(chunk)
                if total > MAX_STREAM_BYTES:
                    raise ValueError("worker stream limit")
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    count += 1
                    if len(line) > (131072 if resident else MAX_LINE_BYTES) or count > MAX_WORKER_EVENTS:
                        raise ValueError('worker event limit')
                    if resident:
                        frame = json.loads(line)
                        if type(frame) is not dict or frame.get('job_id') != job_id:
                            raise ValueError('resident job binding mismatch')
                        if frame.get('type') == 'result':
                            if set(frame) != {'type','job_id','ok','components','measurement'} or type(frame['ok']) is not bool or buffer:
                                raise ValueError('invalid resident completion')
                            components = frame['components']
                            if type(components) is not list or len(components) > 5 or any(c not in {'diffusion_model','text_encoder','video_vae','audio_vae','pipeline'} for c in components):
                                raise ValueError('invalid resident components')
                            m = frame['measurement']
                            if m is not None:
                                from h3server.performance import validate_measurement
                                if type(m) is not dict or set(m) != {'hardware','metrics','cache','actual_attention'} or m['cache'] not in {'cold','warm','warm_partial','encoder_reuse'}:
                                    raise ValueError('invalid resident measurement')
                                validate_measurement(m['metrics'], m['hardware'], m['actual_attention'])
                            return frame
                        if set(frame) != {'type','job_id','event'} or frame['type'] != 'event':
                            raise ValueError('unknown resident frame')
                        line = json.dumps(frame['event']).encode()
                    if len(line) > MAX_LINE_BYTES:
                        raise ValueError('worker event limit')
                    event = decode_event(line.decode('utf-8'))
                    if event.stage not in STAGES or (event.progress and event.progress.unit not in UNITS):
                        raise ValueError("unknown worker event vocabulary")
                    from h3server.installer.diagnostics import safe_log
                    prompt = self.store.get_job(job_id).request.prompt
                    message = event.message
                    for private in (prompt, repr(prompt)[1:-1], json.dumps(prompt)[1:-1], *prompt.splitlines()):
                        if private:
                            message = message.replace(private, '[prompt redacted]')
                    message = safe_log(message)[:4000]
                    event = Event(event.stage, event.status, message, event.progress)
                    with self._lock:
                        if self.store.get_job(job_id).status is JobStatus.RUNNING:
                            self.store.append_event("job", job_id, event)
                if len(buffer) > (131072 if resident else MAX_LINE_BYTES):
                    raise ValueError('worker line limit')

    def _open_output(self, job_id, *, allow_empty=False):
        with _directory(self.paths.root, "tmp", "jobs", _job_id(job_id)) as directory:
            try:
                fd = os.open("video.mp4", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            except FileNotFoundError:
                if not allow_empty:
                    raise
                # Pruned mux atomically publishes only after validation. Enforce
                # the same live byte limit on its unfinished MP4 as legacy output.
                fd = os.open("partial.mp4", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not (0 if allow_empty else 1) <= info.st_size <= self.max_output_bytes:
            os.close(fd)
            raise ValueError("invalid output file")
        return fd

    def open_video(self, job_id: str) -> int:
        with self._lock:
            metadata = self.store.get_job_output(_job_id(job_id))
            if metadata is None or metadata.expired or metadata.filename != "video.mp4" or metadata.media_type != "video/mp4":
                raise FileNotFoundError("output unavailable")
            fd = self._open_output(job_id)
            if os.fstat(fd).st_size != metadata.size_bytes:
                os.close(fd)
                raise ValueError("output changed")
            return fd

    def cleanup_outputs(self):
        """Expire only completed jobs, oldest first. Never delete an active output."""
        with self._lock:
            candidates = []
            offset = 0
            while True:
                jobs = self.store.list_jobs(limit=1000, offset=offset)
                for job in jobs:
                    if job.status is not JobStatus.COMPLETED:
                        if job.status in TERMINAL:
                            self._cleanup_partial(job.job_id)
                        continue
                    metadata = self.store.get_job_output(job.job_id)
                    if metadata is None or metadata.expired:
                        # Recover a crash between durable revocation and unlink.
                        self._cleanup_partial(job.job_id)
                        continue
                    try:
                        fd = self.open_video(job.job_id)
                        try:
                            info = os.fstat(fd)
                        finally:
                            os.close(fd)
                        candidates.append((info.st_mtime, job.job_id, info.st_size))
                    except (OSError, ValueError):
                        self.store.expire_job_output(job.job_id)
                if len(jobs) < 1000:
                    break
                offset += len(jobs)
            total = sum(size for _, _, size in candidates)
            for modified, job_id, size in sorted(candidates):
                if modified + self.retention_seconds < time.time() or total > self.retained_bytes:
                    # Revoke first: a crash before unlink must not make output live.
                    self.store.expire_job_output(job_id)
                    self._cleanup_partial(job_id)
                    total -= size
