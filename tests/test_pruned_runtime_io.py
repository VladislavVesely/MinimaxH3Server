"""Runtime integrity and worker output boundaries using tiny controlled fixtures."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from h3server.backends import pruned, comfy_runtime
from h3server.backends.base import atomic_json
from h3server.paths import AppPaths
from h3server.domain import GenerationSettings, JobRequest
from h3server.remote.worker import Worker
from h3server.store import Store
from test_pruned_runtime import runtime, request  # faithful node fixture


@pytest.fixture
def document(tmp_path):
    paths = AppPaths(tmp_path/'app')
    for d in paths.directories(): d.mkdir(parents=True, exist_ok=True)
    job = paths.tmp/'jobs'/('a'*32); job.mkdir()
    value = dict(schema_version=1, job_id=job.name, request=asdict(request()), root=str(paths.root), output_path=str(job/'video.mp4'))
    atomic_json(paths, f'tmp/jobs/{job.name}/request.json', value)
    return paths, job, value


def fake_gates(monkeypatch, rt, paths):
    calls = []
    monkeypatch.setattr(pruned, 'require_consent', lambda p, **kw: calls.append(('consent',kw)))
    monkeypatch.setattr(pruned, 'verify_snapshot', lambda p, **kw: calls.append(('weights',kw)) or paths.models)
    monkeypatch.setattr(pruned, 'verify_provisioned', lambda *a: calls.append(('runtime',a)) or paths.environments)
    monkeypatch.setattr(pruned, 'load_runtime', lambda *a: rt)
    monkeypatch.setattr(pruned, 'check_gpu', lambda *a: None)
    return calls


def test_atomic_confined_publication_after_validation(document, runtime, monkeypatch):
    paths, job, value = document; rt, _ = runtime
    calls = fake_gates(monkeypatch, rt, paths)
    from h3server.backends import h3
    def validate(path, settings):
        assert Path(path).name == 'partial.mp4'
        assert (job/'partial.mp4').read_bytes().startswith(b'controlled fixture')
        assert not (job/'video.mp4').exists()
    monkeypatch.setattr(h3, 'validate_media', validate)
    pruned.execute_document(job/'request.json', profile=request().settings.profile, emit=lambda e: None)
    assert (job/'video.mp4').read_bytes().startswith(b'controlled fixture')
    assert not (job/'partial.mp4').exists()
    assert [c[0] for c in calls] == ['consent','weights','runtime','consent']
    assert calls[0][1] == calls[1][1] == calls[-1][1] == {'profile_name':request().settings.profile}


@pytest.mark.parametrize('failure', ['native_baseexception','invalid_media','revoked_consent'])
def test_failure_never_publishes_or_leaves_partial(document, runtime, monkeypatch, failure):
    paths, job, _ = document; rt, _ = runtime
    fake_gates(monkeypatch, rt, paths)
    from h3server.backends import h3
    monkeypatch.setattr(h3, 'validate_media', lambda *a: None)
    class NativeInterrupt(BaseException): pass
    def fail(*args): raise NativeInterrupt()
    if failure == 'native_baseexception':
        rt.create_video.execute()[0].save_to = fail
    elif failure == 'invalid_media': monkeypatch.setattr(h3,'validate_media', fail)
    else:
        n = [0]
        def consent(*a, **kw):
            n[0] += 1
            if n[0] == 2: raise ValueError('revoked')
        monkeypatch.setattr(pruned,'require_consent',consent)
    with pytest.raises(ValueError if failure == 'revoked_consent' else NativeInterrupt):
        pruned.execute_document(job/'request.json', emit=lambda e: None)
    assert not (job/'video.mp4').exists() and not (job/'partial.mp4').exists()


@pytest.mark.parametrize('name', ['request.json','partial.mp4','video.mp4'])
def test_symlinks_never_followed(document, runtime, monkeypatch, name, tmp_path):
    paths, job, _ = document; rt, _ = runtime
    fake_gates(monkeypatch,rt,paths)
    outside = tmp_path/'outside'; outside.write_bytes(b'private')
    (job/name).unlink(missing_ok=True); (job/name).symlink_to(outside)
    with pytest.raises((OSError,ValueError)):
        pruned.execute_document(job/'request.json', emit=lambda e: None)
    assert outside.read_bytes() == b'private'


def test_worker_tracks_partial_byte_limit_and_cleanup(document):
    paths, job, _ = document
    worker = Worker(Store(paths.state/'test.sqlite'),paths,max_output_bytes=8)
    (job/'partial.mp4').write_bytes(b'123456789')
    with pytest.raises(ValueError): worker._open_output(job.name,allow_empty=True)
    with pytest.raises(FileNotFoundError): worker._open_output(job.name)
    worker._cleanup_partial(job.name)
    assert list(job.iterdir()) == []


def test_provisioned_integrity_gate_checks_running_environment(tmp_path, monkeypatch):
    paths = AppPaths(tmp_path/'app')
    env = paths.environments/('b'*64); env.mkdir(parents=True)
    profile = request().settings.profile
    resolved = 'torch==2.14.0\n'
    (env/'resolved.txt').write_text(resolved)
    atomic_json(paths, f'environments/{env.name}/runtime.json', comfy_runtime.runtime_record(profile,resolved))
    monkeypatch.setattr(comfy_runtime.sys,'prefix',str(env))
    checks = []
    monkeypatch.setattr(comfy_runtime,'verify_sources',lambda *a: checks.append(a))
    monkeypatch.setattr(comfy_runtime.subprocess,'run',lambda *a, **kw: NS(stdout=resolved.encode()))
    assert comfy_runtime.verify_provisioned(paths,profile) == env
    assert checks == [(env,profile)]
    with pytest.raises(ValueError): comfy_runtime.verify_provisioned(paths,'h3-convrot-mixed')
    monkeypatch.setattr(comfy_runtime.subprocess,'run',lambda *a, **kw: NS(stdout=b'tampered'))
    with pytest.raises(ValueError,match='dependencies'): comfy_runtime.verify_provisioned(paths,profile)


def test_source_tree_fingerprint_includes_extra_files_and_rejects_links(tmp_path):
    root = tmp_path/'code'; root.mkdir(); (root/'file.py').write_text('print(1)')
    digest = comfy_runtime.source_fingerprint(root)
    (root/'extra.py').write_text('print(2)')
    assert comfy_runtime.source_fingerprint(root) != digest
    (root/'extra.py').unlink(); (root/'extra.py').symlink_to(root/'file.py')
    with pytest.raises(ValueError): comfy_runtime.source_fingerprint(root)
    (root/'extra.py').unlink(); (root/'subdir').symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(ValueError): comfy_runtime.source_fingerprint(root)


def test_request_wrong_active_profile_rejected_before_consent(document, monkeypatch):
    _, job, _ = document
    monkeypatch.setattr(pruned,'require_consent',lambda *a, **kw: pytest.fail('must reject first'))
    with pytest.raises(ValueError,match='active profile'):
        pruned.execute_document(job/'request.json',profile='h3-convrot-mixed',emit=lambda e: None)


def test_worker_closes_protocol_reader_even_if_termination_fails(document, monkeypatch):
    import io
    from h3server.remote.worker import Backend
    from h3server.domain import JobStatus
    paths, _, _ = document
    store = Store(paths.state/'test.sqlite')
    worker = Worker(store, paths, backend=Backend(readiness=lambda:()), min_free_bytes=0)
    job = worker.submit(request())
    job = store.claim_next_job()
    process = NS(stdout=io.BytesIO())
    monkeypatch.setattr('h3server.remote.worker.subprocess.Popen',lambda *a, **kw: process)
    def bad_stream(*a): raise ValueError('protocol error')
    def failed_termination(*a): raise OSError('termination failure')
    monkeypatch.setattr(worker,'_consume',bad_stream)
    monkeypatch.setattr(worker,'_terminate',failed_termination)
    with pytest.raises(OSError): worker._execute(job)
    assert process.stdout.closed
    assert store.get_job(job.job_id).status == JobStatus.FAILED
    # Keep the process/ownership handle for stop/service recovery, not a silently
    # orphaned process after termination errors.
    assert worker._process is process


def test_offline_import_only_in_child():
    import subprocess, sys
    subprocess.run([sys.executable,'-c',"import sys; from h3server.backends import pruned; assert not any(n in sys.modules for n in ('torch','nodes','comfy','diffusers','av'))"],check=True)
