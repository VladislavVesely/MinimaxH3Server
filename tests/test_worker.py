"""Explicit fixture subprocesses only: none are a production backend mode."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import sys
import time

import pytest

from h3server.domain import Capabilities, GenerationSettings, JobRequest, JobStatus
from h3server.remote.worker import Backend, QueueFull, Worker
from test_remote_api import service


def fixture_backend(tmp_path, body=None):
    script = tmp_path / "fixture_backend.py"
    script.write_text(body or '''import json, pathlib, sys
request = json.loads(pathlib.Path(sys.argv[2]).read_text())
print(json.dumps({"stage":"denoising", "status":"running", "message":"secret-password", "progress":{"completed":1,"total":2,"unit":"steps"}}), flush=True)
pathlib.Path(request["output_path"]).write_bytes(b"fixture-mp4")
''')
    return Backend(command=(sys.executable, str(script)), readiness=lambda: (), validate_video=lambda fd: os.read(fd, 20) == b"fixture-mp4")


def wait_status(store, job_id, statuses, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = store.get_job(job_id)
        if job.status in statuses:
            return job
        time.sleep(.01)
    raise AssertionError(f"job did not reach {statuses}: {job}")


def test_fixture_process_events_output_and_reopen(tmp_path):
    store, worker, _ = service(tmp_path, backend=fixture_backend(tmp_path), min_free_bytes=0)
    worker.start()
    try:
        job = worker.submit(JobRequest("fixture", settings=GenerationSettings()))
        assert wait_status(store, job.job_id, {JobStatus.COMPLETED, JobStatus.FAILED}).status == JobStatus.COMPLETED
        events = store.read_events().events
        assert any(e.event.progress and e.event.progress.completed == 1 for e in events)
        assert "secret-password" not in str(events)
        assert store.get_job_output(job.job_id).filename == "video.mp4"
        assert not (worker.paths.job_dir(job.job_id) / "request.json").exists()
    finally:
        worker.stop()


def test_transactional_bounded_acceptance(tmp_path):
    store, worker, _ = service(tmp_path, backend=fixture_backend(tmp_path), queue_limit=2, min_free_bytes=0)
    def accept(_):
        try:
            return worker.submit(JobRequest("test", settings=GenerationSettings()))
        except QueueFull:
            return None
    with ThreadPoolExecutor(max_workers=10) as pool:
        accepted = list(pool.map(accept, range(20)))
    assert sum(job is not None for job in accepted) == 2
    assert len(store.list_jobs()) == 2
    assert len(store.read_events().events) == 2


def test_cancel_process_group_and_serial_schedule(tmp_path):
    backend = fixture_backend(tmp_path, '''import json, pathlib, sys, time, subprocess
request = json.loads(pathlib.Path(sys.argv[2]).read_text())
pathlib.Path(request["output_path"]).write_bytes(b"partial")
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
time.sleep(60)
''')
    store, worker, _ = service(tmp_path, backend=backend, min_free_bytes=0)
    worker.start()
    try:
        first = worker.submit(JobRequest("one", settings=GenerationSettings()))
        second = worker.submit(JobRequest("two", settings=GenerationSettings()))
        wait_status(store, first.job_id, {JobStatus.RUNNING})
        time.sleep(.1)
        assert store.get_job(second.job_id).status == JobStatus.QUEUED
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda _: worker.cancel(first.job_id), range(5)))
        assert store.get_job(first.job_id).status == JobStatus.CANCELLED
        assert not worker.paths.job_dir(first.job_id).joinpath("video.mp4").exists()
        wait_status(store, second.job_id, {JobStatus.RUNNING})
        worker.cancel(second.job_id)
    finally:
        worker.stop()


def test_startup_reconciles_and_exclusive_owner(tmp_path):
    store, worker, _ = service(tmp_path)
    job = store.accept_job(JobRequest("test"), capabilities=Capabilities())
    store.transition_job(job.job_id, JobStatus.RUNNING)
    directory = worker.paths.job_dir(job.job_id)
    directory.mkdir()
    (directory / "video.mp4").write_bytes(b"partial")
    worker.start()
    try:
        assert store.get_job(job.job_id).status == JobStatus.INTERRUPTED
        assert not (directory / "video.mp4").exists()
        other = Worker(store, worker.paths)
        with pytest.raises(RuntimeError):
            other.start()
    finally:
        worker.stop()


@pytest.mark.parametrize("body", [
    'print("x" * 20000, flush=True)',
    'print("not json", flush=True)',
    'print(\'{"stage":"secret-token","status":"running","message":"","progress":null}\', flush=True)',
    'import json,pathlib,sys; r=json.loads(pathlib.Path(sys.argv[2]).read_text()); pathlib.Path(r["output_path"]).write_bytes(b"not-mp4")',
])
def test_invalid_worker_stream_or_video_fails_closed(tmp_path, body):
    store, worker, _ = service(tmp_path, backend=fixture_backend(tmp_path, body), min_free_bytes=0)
    worker.start()
    try:
        job = worker.submit(JobRequest("test", settings=GenerationSettings()))
        assert wait_status(store, job.job_id, {JobStatus.FAILED, JobStatus.COMPLETED}).status == JobStatus.FAILED
        assert store.get_job_output(job.job_id) is None
        assert "secret-token" not in str(store.read_events())
    finally:
        worker.stop()


def test_disk_limit_rejects_before_acceptance(tmp_path):
    store, worker, _ = service(tmp_path, backend=fixture_backend(tmp_path), min_free_bytes=10**30)
    with pytest.raises(OSError):
        worker.submit(JobRequest("test", settings=GenerationSettings()))
    assert not store.list_jobs()


def test_cancellation_during_validation_cannot_complete(tmp_path):
    from dataclasses import replace
    import threading
    entered, release = threading.Event(), threading.Event()
    def validate(fd):
        entered.set()
        assert release.wait(3)
        return True
    backend = replace(fixture_backend(tmp_path), validate_video=validate)
    store, worker, _ = service(tmp_path, backend=backend, min_free_bytes=0)
    worker.start()
    try:
        job = worker.submit(JobRequest("fixture", settings=GenerationSettings()))
        assert entered.wait(3)
        assert worker.cancel(job.job_id).status == JobStatus.CANCELLED
        release.set()
    finally:
        release.set()
        worker.stop()
    assert store.get_job(job.job_id).status == JobStatus.CANCELLED
    assert store.get_job_output(job.job_id) is None


def test_zero_length_during_export_is_not_failure(tmp_path):
    backend = fixture_backend(tmp_path, '''import json,pathlib,sys,time
r=json.loads(pathlib.Path(sys.argv[2]).read_text())
p=pathlib.Path(r["output_path"])
p.touch()
time.sleep(.3)
p.write_bytes(b"fixture-mp4")
''')
    store, worker, _ = service(tmp_path, backend=backend, min_free_bytes=0)
    worker.start()
    try:
        job = worker.submit(JobRequest("fixture", settings=GenerationSettings()))
        assert wait_status(store, job.job_id, {JobStatus.COMPLETED, JobStatus.FAILED}).status == JobStatus.COMPLETED
    finally:
        worker.stop()


def test_store_acceptance_bound_across_independent_instances(tmp_path):
    from h3server.store import Store
    store, _, _ = service(tmp_path)
    def accept(_):
        try:
            return Store(store.path).accept_job(JobRequest("test"), capabilities=Capabilities(), pending_limit=2)
        except QueueFull:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(job is not None for job in pool.map(accept, range(16))) == 2
    assert len(store.read_events().events) == 2


@pytest.mark.parametrize("limit", ["bytes", "runtime"])
def test_worker_resource_limits_fail_and_remove_output(tmp_path, limit):
    body = '''import json,pathlib,sys,time
r=json.loads(pathlib.Path(sys.argv[2]).read_text())
pathlib.Path(r["output_path"]).write_bytes(b"x"*200)
time.sleep(60)
'''
    options = {"max_output_bytes": 100} if limit == "bytes" else {"max_runtime_seconds": .15}
    store, worker, _ = service(tmp_path, backend=fixture_backend(tmp_path, body), min_free_bytes=0, **options)
    worker.start()
    try:
        job = worker.submit(JobRequest("fixture", settings=GenerationSettings()))
        assert wait_status(store, job.job_id, {JobStatus.FAILED}).status == JobStatus.FAILED
    finally:
        worker.stop()
    assert not (worker.paths.job_dir(job.job_id) / "video.mp4").exists()


def test_default_mp4_validator_uses_real_decoder(tmp_path):
    import shutil
    import subprocess
    from h3server.remote.worker import validate_mp4
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("optional local ffmpeg fixture generator/ffprobe unavailable")
    video = tmp_path / "explicit-test-fixture.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=32x32:d=0.2", "-c:v", "libx264", str(video)], check=True)
    with video.open("rb") as stream:
        assert validate_mp4(stream.fileno())
    video.write_bytes(b"not an mp4")
    with video.open("rb") as stream:
        assert not validate_mp4(stream.fileno())


def test_crashed_daemon_cannot_overlap_inherited_worker_lock(tmp_path):
    """Real process crash fixture; no PID-based recovery or model execution."""
    import signal
    import subprocess
    backend = fixture_backend(tmp_path, "import time; time.sleep(60)")
    store, worker, _ = service(tmp_path, backend=backend, min_free_bytes=0)
    harness = tmp_path / "daemon-fixture.py"
    harness.write_text(f'''import pathlib, time
from h3server.domain import GenerationSettings, JobRequest
from h3server.paths import AppPaths
from h3server.store import Store
from h3server.remote.worker import Backend, Worker
root=pathlib.Path({str(tmp_path)!r})
w=Worker(Store(root / "state" / "remote.sqlite"), AppPaths(root), backend=Backend(command={backend.command!r}, readiness=lambda: ()), min_free_bytes=0)
w.start()
j=w.submit(JobRequest("fixture", settings=GenerationSettings()))
while w._process is None: time.sleep(.01)
(root / "ready.json").write_text(__import__("json").dumps([j.job_id, w._process.pid]))
time.sleep(60)
''')
    daemon = subprocess.Popen([sys.executable, str(harness)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    child_pid = None
    try:
        ready = tmp_path / "ready.json"
        deadline = time.monotonic() + 5
        while not ready.exists():
            assert time.monotonic() < deadline and daemon.poll() is None
            time.sleep(.01)
        job_id, child_pid = json.loads(ready.read_text())
        daemon.kill()
        daemon.wait(timeout=5)
        with pytest.raises(RuntimeError, match="ownership"):
            worker.start()
        os.killpg(child_pid, signal.SIGKILL)
        child_pid = None
        deadline = time.monotonic() + 5
        while True:
            try:
                worker.start()
                break
            except RuntimeError:
                assert time.monotonic() < deadline
                time.sleep(.01)
        assert store.get_job(job_id).status == JobStatus.INTERRUPTED
    finally:
        if child_pid is not None:
            os.killpg(child_pid, signal.SIGKILL)
        if daemon.poll() is None:
            daemon.kill()
        daemon.wait(timeout=5)
        worker.stop()


def test_scheduler_failure_stops_acceptance_without_raw_errors(tmp_path, monkeypatch, capsys):
    from h3server.remote.worker import BackendUnavailable
    store, worker, _ = service(tmp_path, backend=fixture_backend(tmp_path), min_free_bytes=0)
    original = worker.cleanup_outputs
    calls = 0
    def broken_cleanup():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OSError("credential-that-must-not-be-logged")
        original()
    monkeypatch.setattr(worker, "cleanup_outputs", broken_cleanup)
    worker.start()
    try:
        deadline = time.monotonic() + 3
        while not worker._stopping.is_set():
            assert time.monotonic() < deadline
            time.sleep(.01)
        assert worker.readiness() == ("Worker scheduler is not available",)
        with pytest.raises(BackendUnavailable):
            worker.submit(JobRequest("test", settings=GenerationSettings()))
    finally:
        worker.stop()
    assert "credential-that-must-not-be-logged" not in str(capsys.readouterr())


def test_group_termination_is_serial_idempotent_and_precedes_reaping(tmp_path, monkeypatch):
    import subprocess
    _, worker, _ = service(tmp_path)
    process = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    worker._wait_exit(process, 3)
    calls = []
    original = os.killpg
    def signal_group(pid, sig):
        # The leader is still ours and waitable: its ID cannot have been reused.
        assert os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        calls.append((pid, sig))
        original(pid, sig)
    monkeypatch.setattr(os, "killpg", signal_group)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: worker._terminate(process), range(16)))
        assert len(calls) == 2
        assert process.returncode == 0
    finally:
        process.wait(timeout=3)
