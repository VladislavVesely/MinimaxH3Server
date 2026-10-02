import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from h3server.domain import JobRequest, GenerationSettings, JobStatus
from test_remote_api import service, HEADERS
from test_resident import resident_backend
from test_worker import wait_status

DEFAULT = dict(reserve_vram_gib=None, offload_mode='automatic', expandable_segments=False)
CHANGED = dict(reserve_vram_gib=4.0, offload_mode='low_vram', expandable_segments=True)
ROUTE = '/api/v1/settings/memory'


def test_defaults_persistence_and_revision(tmp_path):
    _, worker, _ = service(tmp_path, backend=resident_backend(tmp_path), min_free_bytes=0)
    first = worker.memory_settings()
    assert first['settings'] == DEFAULT
    assert not (tmp_path / 'config/runtime-memory.json').exists()
    updated = worker.update_memory_settings(CHANGED, first['revision'])
    assert updated['settings'] == CHANGED and updated['revision'] != first['revision']
    assert (tmp_path / 'config/runtime-memory.json').stat().st_mode & 0o777 == 0o600
    _, restarted, _ = service(tmp_path, backend=resident_backend(tmp_path), min_free_bytes=0)
    assert restarted.memory_settings()['settings'] == CHANGED
    with pytest.raises(ValueError, match='changed'):
        restarted.update_memory_settings(DEFAULT, first['revision'])
    assert restarted.memory_settings()['settings'] == CHANGED


@pytest.mark.parametrize('patch', [dict(reserve_vram_gib=True), dict(reserve_vram_gib=-1),dict(reserve_vram_gib=65),dict(reserve_vram_gib='4'),dict(reserve_vram_gib=float('inf')),dict(offload_mode='gpu_only'),dict(expandable_segments='True'),dict(arbitrary_env='evil')])
def test_invalid_policy_never_changes_saved_state(tmp_path, patch):
    _, worker, _ = service(tmp_path, backend=resident_backend(tmp_path), min_free_bytes=0)
    first = worker.memory_settings()
    with pytest.raises(ValueError):
        worker.update_memory_settings(DEFAULT | patch, first['revision'])
    assert worker.memory_settings()['settings'] == DEFAULT


def test_busy_and_maintenance_apply_never_cancels_jobs(tmp_path):
    store, worker, _ = service(tmp_path, backend=resident_backend(tmp_path), min_free_bytes=0)
    revision = worker.memory_settings()['revision']
    job = worker.submit(JobRequest('TEST ONLY', settings=GenerationSettings()))
    with pytest.raises(ValueError, match='busy'):
        worker.update_memory_settings(CHANGED, revision)
    assert store.get_job(job.job_id).status == JobStatus.QUEUED
    store.transition_job(job.job_id, JobStatus.CANCELLED)
    worker.begin_maintenance('a' * 32, policy='reject')
    with pytest.raises(ValueError, match='busy'):
        worker.update_memory_settings(CHANGED, revision)
    assert worker.memory_settings()['settings'] == DEFAULT


def test_apply_releases_real_idle_resident_process(tmp_path):
    store, worker, _ = service(tmp_path, backend=resident_backend(tmp_path), min_free_bytes=0)
    worker.start()
    try:
        job = worker.submit(JobRequest('TEST ONLY', settings=GenerationSettings()))
        assert wait_status(store, job.job_id, {JobStatus.COMPLETED, JobStatus.FAILED}).status == JobStatus.COMPLETED
        with worker._lock:
            old = worker._resident_process
            worker.update_memory_settings(CHANGED, worker.memory_settings()['revision'])
            assert old.poll() is not None
            assert not worker.models_status()['loaded']
            assert worker._hardware is None
    finally:
        worker.stop()


def test_startup_environment_and_comfy_flags(tmp_path, monkeypatch):
    from h3server.runtime_memory import configure_comfy
    _, worker, _ = service(tmp_path, backend=resident_backend(tmp_path), min_free_bytes=0)
    monkeypatch.setenv('PYTORCH_ALLOC_CONF', 'inherited-untrusted')
    monkeypatch.setenv('PYTORCH_CUDA_ALLOC_CONF', 'inherited-untrusted')
    worker.update_memory_settings(CHANGED, worker.memory_settings()['revision'])
    env = worker._environment()
    assert env['PYTORCH_CUDA_ALLOC_CONF'] == 'expandable_segments:True'
    assert 'PYTORCH_ALLOC_CONF' not in env
    monkeypatch.setenv('H3_RUNTIME_MEMORY_SETTINGS', env['H3_RUNTIME_MEMORY_SETTINGS'])
    args = SimpleNamespace(reserve_vram=None, lowvram=False)
    configure_comfy(args)
    assert args.reserve_vram == 4.0 and args.lowvram is True
    worker.update_memory_settings(DEFAULT, worker.memory_settings()['revision'])
    assert 'PYTORCH_CUDA_ALLOC_CONF' not in worker._environment()


def test_settings_api_auth_validation_and_busy(tmp_path):
    _, worker, app = service(tmp_path, backend=resident_backend(tmp_path), min_free_bytes=0)
    with TestClient(app) as client:
        assert client.get(ROUTE).status_code == 401
        first = client.get(ROUTE, headers=HEADERS).json()
        assert client.post(ROUTE, headers=HEADERS, json={'settings':CHANGED,'revision':first['revision'],'unknown':1}).status_code == 422
        result = client.post(ROUTE, headers=HEADERS, json={'settings':CHANGED,'revision':first['revision']})
        assert result.status_code == 200 and result.json()['settings'] == CHANGED
        assert client.post(ROUTE, headers=HEADERS, json={'settings':DEFAULT,'revision':first['revision']}).status_code == 409
        worker.begin_maintenance('b' * 32, policy='reject')
        assert client.post(ROUTE, headers=HEADERS, json={'settings':DEFAULT,'revision':result.json()['revision']}).status_code == 409


def test_memory_policy_changes_timing_configuration(tmp_path):
    from h3server.performance import configuration
    _, worker, _ = service(tmp_path, backend=resident_backend(tmp_path), min_free_bytes=0)
    request = JobRequest('TEST ONLY', settings=GenerationSettings())
    before = configuration(request, worker.paths)
    worker.update_memory_settings(CHANGED, worker.memory_settings()['revision'])
    after = configuration(request, worker.paths)
    assert before != after and after['runtime_memory'] == CHANGED


def test_apply_and_submit_are_serialized_without_cancellation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store, worker, _ = service(tmp_path, backend=resident_backend(tmp_path), min_free_bytes=0)
    revision = worker.memory_settings()['revision']
    def apply():
        try: return worker.update_memory_settings(CHANGED, revision)
        except ValueError as error: return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(apply)
        b = pool.submit(worker.submit, JobRequest('TEST ONLY', settings=GenerationSettings()))
        result, job = a.result(), b.result()
    assert store.get_job(job.job_id).status == JobStatus.QUEUED
    assert isinstance(result, dict) or result == 'memory settings busy'


def test_runtime_flags_are_set_before_model_management_import():
    from pathlib import Path
    from h3server.backends import pruned
    source = Path(pruned.__file__).read_text()
    assert source.index('memory_policy = configure_comfy(args)') < source.index('import comfy.model_management')


def test_corrupt_or_symlink_policy_fails_closed(tmp_path):
    from h3server.runtime_memory import load
    _, worker, _ = service(tmp_path, backend=resident_backend(tmp_path), min_free_bytes=0)
    path = tmp_path / 'config/runtime-memory.json'
    path.write_text('{}'); path.chmod(0o600)
    with pytest.raises(ValueError): load(worker.paths)
    path.unlink(); path.symlink_to(tmp_path / 'elsewhere')
    with pytest.raises((ValueError, OSError)): load(worker.paths)
