"""Remote API contracts; fixture readiness never represents H3 execution."""
import json

from fastapi.testclient import TestClient
import pytest

from h3server.domain import GenerationSettings, JobRequest
from h3server.paths import AppPaths
from h3server.remote.app import create_app, load_token
from h3server.remote.worker import Worker
from h3server.store import Store

TOKEN = "test-only-remote-token-" + "x" * 32
HEADERS = {"Authorization": "Bearer " + TOKEN}


def service(tmp_path, **kwargs):
    paths = AppPaths(tmp_path)
    for path in paths.directories():
        path.mkdir(parents=True, exist_ok=True)
    store = Store(paths.state / "remote.sqlite", event_retention=kwargs.pop("event_retention", 100))
    worker = Worker(store, paths, **kwargs)
    return store, worker, create_app(store, worker, token=TOKEN)


def test_auth_all_routes_and_openapi(tmp_path):
    store, worker, app = service(tmp_path)
    with TestClient(app) as client:
        for path in ("/api/v1/health", "/api/v1/capabilities", "/api/v1/jobs", "/api/v1/events", "/openapi.json"):
            response = client.get(path)
            assert response.status_code == 401
            assert TOKEN not in response.text
        schema = client.get("/openapi.json", headers=HEADERS).json()
        assert "/api/v1/jobs/{job_id}/video" in schema["paths"]
        health = client.get("/api/v1/health", headers=HEADERS).json()
        assert health["runtime_ready"] is False
        assert health["generation_tested"] is False
        response = client.post("/api/v1/jobs", headers=HEADERS, json={"prompt": "test"})
        assert response.status_code == 503
        assert not store.list_jobs()


@pytest.mark.parametrize("extra", [
    {"unknown": 1}, {"negative_prompt": "no"}, {"sparse_attention": False},
    {"num_frames": True}, {"width": True}, {"steps": True}, {"seed": True},
    {"width": 33}, {"width": 1344, "height": 1344}, {"width": 1376},
    {"num_frames": 346}, {"duration": 16}, {"duration": 4},
    {"duration": 5, "num_frames": 124}, {"steps": 1}, {"seed": -1},
    {"profile": "invented"}, {"loras": ["unavailable"]}, {"prompt": " "},
])
def test_strict_api_validation(tmp_path, extra):
    _, _, app = service(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/v1/jobs", headers=HEADERS, json={"prompt": "test", **extra})
        assert response.status_code == 422
        assert TOKEN not in response.text
        assert "input" not in response.text


@pytest.mark.parametrize("geometry", [(1344, 768), (768, 1344), (992, 992)])
def test_geometry_and_normalization(tmp_path, geometry):
    _, _, app = service(tmp_path)
    schema = app.state.request_model
    value = schema(prompt="test", duration=5, width=geometry[0], height=geometry[1])
    request = value.to_request()
    assert request.settings.num_frames == 124
    assert request.settings.duration == 124 / 24
    assert schema(prompt="test", num_frames=125).to_request().settings.num_frames == 124
    assert schema(prompt="test", num_frames=135).to_request().settings.num_frames == 141
    assert schema(prompt="test", duration=15).to_request().settings.num_frames == 345
    assert schema(prompt="test", num_frames=345).to_request().settings.num_frames == 345


def test_settings_restart_and_legacy_json(tmp_path):
    store, _, _ = service(tmp_path)
    from h3server.domain import Capabilities
    settings = GenerationSettings(num_frames=141, width=768, height=1344, steps=7, seed=12)
    job = store.accept_job(JobRequest("test", settings=settings), capabilities=Capabilities())
    assert Store(store.path).get_job(job.job_id).request.settings == settings
    legacy = store.accept_job(JobRequest("old"), capabilities=Capabilities())
    with store._transaction(write=True) as db:
        db.execute("UPDATE jobs SET request=? WHERE job_id=?", (json.dumps({"prompt": "old", "loras": [], "sparse_attention": None}), legacy.job_id))
    assert Store(store.path).get_job(legacy.job_id).request.settings is None


def test_token_owner_only_and_symlink_rejected(tmp_path):
    paths = AppPaths(tmp_path)
    paths.config.mkdir()
    token = load_token(paths)
    assert len(token) >= 32
    assert load_token(paths) == token
    target = paths.config / "remote-token"
    assert target.stat().st_mode & 0o777 == 0o600
    target.chmod(0o644)
    with pytest.raises(ValueError):
        load_token(paths)
    target.unlink()
    secret = tmp_path / "secret"
    secret.write_text(token)
    target.symlink_to(secret)
    with pytest.raises((OSError, ValueError)):
        load_token(paths)


def test_event_replay_reset_and_latest_state(tmp_path):
    store, worker, app = service(tmp_path, event_retention=2)
    from h3server.domain import Capabilities
    for _ in range(3):
        store.accept_job(JobRequest("test"), capabilities=Capabilities())
    with TestClient(app) as client:
        response = client.get("/api/v1/events?follow=false", headers={**HEADERS, "Last-Event-ID": "0"})
        assert response.status_code == 200
        assert "event: reset" in response.text
        assert '"minimum_cursor":1' in response.text
        assert '"refresh":"/api/v1/jobs"' in response.text
        assert "id: 3" in response.text
        response = client.get("/api/v1/events?follow=false", headers={**HEADERS, "Last-Event-ID": "2"})
        assert "id: 3" in response.text and "id: 2" not in response.text
        assert client.get("/api/v1/events?after=99", headers=HEADERS).status_code == 422
        assert len(client.get("/api/v1/jobs?limit=2", headers=HEADERS).json()["jobs"]) == 2
        assert client.get("/api/v1/jobs?limit=1001", headers=HEADERS).status_code == 422


@pytest.mark.parametrize("extra", [
    {"duration": True}, {"duration": None}, {"num_frames": None},
    {"width": "768"}, {"height": 31}, {"num_frames": 119}, {"steps": 1001},
    {"seed": 2**63}, {"prompt": 123}, {"loras": ["../secret"]},
    {"sparse_attention": None},
])
def test_malformed_options_never_reflected(tmp_path, extra):
    _, _, app = service(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/v1/jobs", headers=HEADERS, json={"prompt": "credential-string", **extra})
        assert response.status_code == 422
        assert "credential-string" not in response.text
        assert response.json() == {"error": "invalid_request"}


def test_configuration_precedence_and_private_file(tmp_path):
    from h3server.remote.__main__ import configuration
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"root": "~/original", "port": 7777, "queue_limit": 3}))
    path.chmod(0o600)
    assert configuration(["--config", str(path), "--root", "~/override", "--port", "8888"]) == ("~/override", 8888, {"queue_limit": 3})
    assert configuration(["--config", str(path)]) == ("~/original", 7777, {"queue_limit": 3})
    path.chmod(0o644)
    with pytest.raises(ValueError):
        configuration(["--config", str(path)])
    with pytest.raises(SystemExit):
        configuration(["--token", "never-accepted"])


def test_real_cli_loopback_sse_heartbeat_disconnect(tmp_path):
    """A real local daemon, no weights, no server/SSH access and no generation."""
    import httpx
    import socket
    import subprocess
    import sys
    import time
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen([sys.executable, "-m", "h3server.remote", "--root", str(tmp_path), "--port", str(port)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 10
        while True:
            try:
                response = httpx.get(url + "/api/v1/health", timeout=.2)
                break
            except httpx.TransportError:
                if time.monotonic() > deadline or process.poll() is not None:
                    raise AssertionError("CLI did not start")
                time.sleep(.05)
        assert response.status_code == 401
        token = (tmp_path / "config" / "remote-token").read_text().strip()
        headers = {"Authorization": "Bearer " + token}
        response = httpx.get(url + "/api/v1/health", headers=headers)
        assert response.json()["runtime_ready"] is False
        with httpx.stream("GET", url + "/api/v1/events", headers=headers, timeout=20) as response:
            assert next(response.iter_lines()) == ": heartbeat"
        # Closing the SSE transport leaves the service healthy, not a stuck worker.
        assert httpx.get(url + "/api/v1/jobs", headers=headers).status_code == 200
        assert httpx.get(url + "/api/v1/health", headers={**headers, "Origin": "https://attacker.invalid"}).status_code == 403
    finally:
        process.terminate()
        stdout, stderr = process.communicate(timeout=10)
    assert not stdout and not stderr  # no tokens, access URLs or tracebacks logged


def test_fixture_api_accept_detail_cancel(tmp_path):
    from test_worker import fixture_backend, wait_status
    from h3server.domain import JobStatus
    store, worker, app = service(tmp_path, backend=fixture_backend(tmp_path), min_free_bytes=0)
    with TestClient(app) as client:
        response = client.post("/api/v1/jobs", headers=HEADERS, json={"prompt": "fixture only", "duration": 5, "seed": 8})
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        wait_status(store, job_id, {JobStatus.COMPLETED})
        detail = client.get(f"/api/v1/jobs/{job_id}", headers=HEADERS).json()
        assert detail["request"]["settings"]["seed"] == 8
        assert detail["effective_duration"] == 124 / 24
        assert client.post(f"/api/v1/jobs/{job_id}/cancel", headers=HEADERS).json()["status"] == "completed"
        assert client.get(f"/api/v1/jobs/{'f'*32}", headers=HEADERS).status_code == 404
