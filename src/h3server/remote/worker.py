"""Single-process scheduler and isolated, bounded JSONL backend protocol.

Task10 adapter contract: ``python -m h3server.backends.h3 --request PATH``.
PATH is a daemon-created 0600 JSON document with schema_version=1, job_id,
request (prompt, loras, sparse_attention, normalized settings), output_path and
root. The adapter must use local verified artifacts, enforce persisted license
consent, write ONLY output_path, and emit domain Event JSON lines on stdout.
Allowed stages/units are below. Messages are deliberately discarded: raw backend
logs, exception text, prompts and credentials never enter the public event log.
Stderr is discarded. Exit zero is necessary but not sufficient: the daemon checks
the bounded regular output and independently validates its MP4 video stream.

Backend injection is a Python composition seam for the next adapter and explicit
subprocess tests, never a CLI/environment fake-production switch. Readiness must
check the actual environment, consent and artifact integrity, not source support.
The default is intentionally unavailable until that adapter supplies these checks.

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

from h3server.domain import Capabilities, Event, JobRequest, JobStatus
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
                 retained_bytes: int = 20 * 1024**3, max_runtime_seconds: float = 6 * 3600):
        for value in (queue_limit, max_output_bytes, retained_bytes):
            if type(value) is not int or value < 1:
                raise ValueError("worker limits must be positive integers")
        if type(min_free_bytes) is not int or min_free_bytes < 0:
            raise ValueError("free-space reserve must be nonnegative")
        for value in (retention_seconds, max_runtime_seconds):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("worker durations must be positive and finite")
        self.store, self.paths = store, paths
        self.backend = backend or Backend()
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

    def readiness(self) -> tuple[str, ...]:
        if self._stopping.is_set() or (self._thread is not None and not self._thread.is_alive()):
            return ("Worker scheduler is not available",)
        try:
            reasons = self.backend.readiness()
            if not isinstance(reasons, tuple) or any(not isinstance(item, str) for item in reasons):
                raise ValueError("invalid readiness result")
            return reasons
        except Exception:
            return ("Backend readiness check failed",)

    def _disk_check(self):
        if shutil.disk_usage(self.paths.root).free < self.min_free_bytes + self.max_output_bytes:
            raise OSError("insufficient output disk reserve")

    def submit(self, request: JobRequest):
        request.validate_capabilities(self.backend.capabilities)
        if request.settings is None:
            raise ValueError("normalized generation settings are required")
        with self._lock:
            if self._stopping.is_set() or self.readiness():
                raise BackendUnavailable("backend is not ready")
            self.cleanup_outputs()
            self._disk_check()
            job = self.store.accept_job(request, capabilities=self.backend.capabilities, pending_limit=self.queue_limit)
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
                self._cleanup_partial(job.job_id)
        if self._thread is not None:
            self._thread.join()
            self._thread = None
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
                job = None if self._stopping.is_set() or self.readiness() else self.store.claim_next_job()
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
                for name in ("request.json", "video.mp4"):
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
        result.update({"HOME": str(self.paths.root), "PYTHONUNBUFFERED": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
        return result

    def _execute(self, job):
        process = None
        try:
            with self._lock:
                if self.store.get_job(job.job_id).status is not JobStatus.RUNNING:
                    return
                self._disk_check()
                request_path = self._prepare(job)
                process = subprocess.Popen(
                    [*self.backend.command, "--request", str(request_path)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    env=self._environment(), cwd=self.paths.root, start_new_session=True,
                    pass_fds=(self._owner_fd,) if self._owner_fd is not None else (),
                )
                self._process = process
            self._consume(process, job.job_id)
            with self._lock:
                if self.store.get_job(job.job_id).status is not JobStatus.RUNNING:
                    return
                result = self._wait_exit(process, 5)
                self._terminate(process)
                if result.si_code != os.CLD_EXITED or result.si_status != 0:
                    raise ValueError("backend failed")
                fd = self._open_output(job.job_id)
            # Parsing may be slow. Cancellation can still acquire the scheduler
            # lock, kill the process group and revoke/delete the partial output.
            try:
                info = os.fstat(fd)
                if not self.backend.validate_video(fd):
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
            finally:
                os.close(fd)
        except Exception:
            # Backend exception text can contain secrets; publish only fixed codes.
            with self._lock:
                if self.store.get_job(job.job_id).status is JobStatus.RUNNING:
                    self.store.transition_job(job.job_id, JobStatus.FAILED)
        finally:
            self._terminate(process)
            if process is not None and process.stdout is not None:
                process.stdout.close()
            with self._lock:
                if self.store.get_job(job.job_id).status is not JobStatus.COMPLETED:
                    self._cleanup_partial(job.job_id)
                self._active = self._process = None

    def _consume(self, process, job_id):
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
                    if len(line) > MAX_LINE_BYTES or count > MAX_WORKER_EVENTS:
                        raise ValueError("worker event limit")
                    event = decode_event(line.decode("utf-8"))
                    if event.stage not in STAGES or (event.progress and event.progress.unit not in UNITS):
                        raise ValueError("unknown worker event vocabulary")
                    event = Event(event.stage, event.status, "", event.progress)
                    with self._lock:
                        if self.store.get_job(job_id).status is JobStatus.RUNNING:
                            self.store.append_event("job", job_id, event)
                if len(buffer) > MAX_LINE_BYTES:
                    raise ValueError("worker line limit")

    def _open_output(self, job_id, *, allow_empty=False):
        with _directory(self.paths.root, "tmp", "jobs", _job_id(job_id)) as directory:
            fd = os.open("video.mp4", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
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
