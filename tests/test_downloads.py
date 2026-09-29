import os
import time

from fastapi.testclient import TestClient
import pytest

from h3server.domain import Capabilities, GenerationSettings, JobRequest, JobStatus
from h3server.store import OutputMetadata
from test_remote_api import HEADERS, service


def completed(store, worker, content=b"fixture-video"):
    job = store.accept_job(JobRequest("test", settings=GenerationSettings()), capabilities=Capabilities())
    directory = worker.paths.job_dir(job.job_id)
    directory.mkdir()
    (directory / "video.mp4").write_bytes(content)
    store.transition_job(job.job_id, JobStatus.RUNNING)
    store.transition_job(job.job_id, JobStatus.COMPLETED, output=OutputMetadata("video.mp4", len(content), "video/mp4"))
    return job, directory


def test_range_preview_and_download(tmp_path):
    store, worker, app = service(tmp_path)
    job, _ = completed(store, worker)
    path = f"/api/v1/jobs/{job.job_id}/video"
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        full = client.get(path, headers=HEADERS)
        assert full.content == b"fixture-video"
        assert full.headers["accept-ranges"] == "bytes"
        response = client.get(path, headers={**HEADERS, "Range": "bytes=1-3"})
        assert response.status_code == 206 and response.content == b"ixt"
        assert response.headers["content-range"] == "bytes 1-3/13"
        assert client.get(path, headers={**HEADERS, "Range": "bytes=-5"}).content == b"video"
        assert client.get(path, headers={**HEADERS, "Range": "bytes=8-"}).content == b"video"
        for value in ("bytes=100-", "bytes=2-1", "bytes=1-2,4-5", "bad"):
            response = client.get(path, headers={**HEADERS, "Range": value})
            assert response.status_code == 416
            assert response.headers["content-range"] == "bytes */13"
        assert "attachment" in client.get(path + "?download=true", headers=HEADERS).headers["content-disposition"]


@pytest.mark.parametrize("kind", ["file", "directory", "missing", "size", "fifo"])
def test_unsafe_outputs_are_never_served(tmp_path, kind):
    store, worker, app = service(tmp_path)
    job, directory = completed(store, worker)
    output = directory / "video.mp4"
    output.unlink()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "video.mp4").write_bytes(b"secret-value!")
    if kind == "file":
        output.symlink_to(outside / "video.mp4")
    elif kind == "directory":
        directory.rmdir()
        directory.symlink_to(outside, target_is_directory=True)
    elif kind == "size":
        output.write_bytes(b"changed")
    elif kind == "fifo":
        os.mkfifo(output)
    with TestClient(app) as client:
        response = client.get(f"/api/v1/jobs/{job.job_id}/video", headers=HEADERS)
        assert response.status_code == 410
        assert b"secret-value!" not in response.content
        assert client.get("/api/v1/jobs/not-a-uuid/video", headers=HEADERS).status_code == 422


def test_retention_marks_expired_and_excludes_running(tmp_path):
    store, worker, app = service(tmp_path, retention_seconds=1)
    old, directory = completed(store, worker)
    os.utime(directory / "video.mp4", (1, 1))
    active = store.accept_job(JobRequest("active"), capabilities=Capabilities())
    active_dir = worker.paths.job_dir(active.job_id)
    active_dir.mkdir()
    (active_dir / "video.mp4").write_bytes(b"partial")
    store.transition_job(active.job_id, JobStatus.RUNNING)
    worker.cleanup_outputs()
    assert store.get_job_output(old.job_id).expired
    assert (active_dir / "video.mp4").exists()
    with TestClient(app) as client:
        response = client.get(f"/api/v1/jobs/{old.job_id}", headers=HEADERS)
        assert response.json()["output"]["expired"] is True
        assert client.get(f"/api/v1/jobs/{old.job_id}/video", headers=HEADERS).status_code == 410


def test_open_descriptor_survives_path_swap(tmp_path):
    store, worker, _ = service(tmp_path)
    job, directory = completed(store, worker)
    fd = worker.open_video(job.job_id)
    try:
        (directory / "video.mp4").unlink()
        (directory / "video.mp4").symlink_to(tmp_path / "secret")
        assert os.read(fd, 100) == b"fixture-video"
    finally:
        os.close(fd)


def test_output_revocation_crash_recovery_and_partial_cleanup(tmp_path):
    store, worker, _ = service(tmp_path)
    job, directory = completed(store, worker)
    store.expire_job_output(job.job_id)  # simulated crash before filesystem unlink
    failed = store.accept_job(JobRequest("test"), capabilities=Capabilities())
    failed_dir = worker.paths.job_dir(failed.job_id)
    failed_dir.mkdir()
    (failed_dir / "video.mp4").write_bytes(b"partial")
    store.transition_job(failed.job_id, JobStatus.FAILED)
    worker.start()
    worker.stop()
    assert not (directory / "video.mp4").exists()
    assert not (failed_dir / "video.mp4").exists()


def test_retained_byte_budget_expires_oldest(tmp_path):
    store, worker, _ = service(tmp_path, retained_bytes=15)
    first, directory = completed(store, worker)
    os.utime(directory / "video.mp4", (time.time() - 10, time.time() - 10))
    second, _ = completed(store, worker)
    worker.cleanup_outputs()
    assert store.get_job_output(first.job_id).expired
    assert not store.get_job_output(second.job_id).expired


def test_legacy_output_metadata_remains_readable(tmp_path):
    import json
    from h3server.store import Store
    store, worker, _ = service(tmp_path)
    job, _ = completed(store, worker)
    with store._transaction(write=True) as db:
        db.execute("UPDATE jobs SET output=? WHERE job_id=?", (json.dumps({"filename": "video.mp4", "size_bytes": 13, "media_type": "video/mp4"}), job.job_id))
    assert Store(store.path).get_job_output(job.job_id).expired is False
