"""Real fixture child processes, never production inference or installers."""
from dataclasses import replace
import os
import sys

import pytest

from h3server.domain import GenerationSettings, JobRequest, JobStatus
from h3server.remote.worker import Backend, BackendUnavailable
from test_remote_api import service
from test_worker import wait_status

A = 'h3-convrot-int8'
B = 'h3-gguf-q5-k-m'


def child(tmp_path, profile, resident):
    script = tmp_path / (profile + '.py')
    script.write_text('''import json, pathlib, sys
profile = ''' + repr(profile) + '''
def output(path):
    data = json.loads(pathlib.Path(path).read_text())
    assert data['request']['settings']['profile'] == profile
    pathlib.Path(data['output_path']).write_bytes(profile.encode())
if sys.argv[1] == '--resident':
    for line in sys.stdin:
        message = json.loads(line)
        output(message['request_path'])
        print(json.dumps(dict(type='result', job_id=message['job_id'], ok=True, components=[], measurement=None)), flush=True)
else:
    assert sys.argv[1] == '--request'
    output(sys.argv[2])
''')
    return Backend(command=(sys.executable, str(script)), readiness=lambda: (), profiles=(profile,),
                   resident=resident, validate_video=lambda fd: os.read(fd, 256) == profile.encode())


def test_each_job_uses_its_backend_protocol_command_and_media_validator(tmp_path):
    children = {A: child(tmp_path, A, True), B: child(tmp_path, B, False)}
    root = Backend(profiles=(A, B), readiness=lambda: ('daemon environment is not a model runtime',),
                   validate_video=lambda fd: False, backend_for_profile=children.__getitem__)
    store, worker, _ = service(tmp_path, backend=root, min_free_bytes=0)
    worker.start()
    try:
        for profile in (A, B, A):
            job = worker.submit(JobRequest('fixture', settings=GenerationSettings(profile=profile)))
            assert wait_status(store, job.job_id, {JobStatus.COMPLETED, JobStatus.FAILED}).status is JobStatus.COMPLETED
    finally:
        worker.stop()


def test_selected_runtime_readiness_blocks_only_its_own_submission(tmp_path):
    children = {A: child(tmp_path, A, False), B: replace(child(tmp_path, B, False), readiness=lambda: ('runtime incomplete',))}
    root = Backend(readiness=lambda: (), backend_for_profile=children.__getitem__)
    store, worker, _ = service(tmp_path, backend=root, min_free_bytes=0)
    with pytest.raises(BackendUnavailable):
        worker.submit(JobRequest('fixture', settings=GenerationSettings(profile=B)))
    assert not store.list_jobs()
    assert worker.submit(JobRequest('fixture', settings=GenerationSettings(profile=A))).status is JobStatus.QUEUED


def test_selector_cannot_route_to_a_different_profiles_runtime(tmp_path):
    wrong = child(tmp_path, A, False)
    root = Backend(readiness=lambda: (), backend_for_profile=lambda profile: wrong)
    store, worker, _ = service(tmp_path, backend=root, min_free_bytes=0)
    with pytest.raises(ValueError, match='profile'):
        worker.submit(JobRequest('fixture', settings=GenerationSettings(profile=B)))
    assert not store.list_jobs()


def test_same_profile_new_environment_recycles_resident_process(tmp_path):
    first = child(tmp_path, A, True)
    second_dir = tmp_path / 'replacement'
    second_dir.mkdir()
    second = child(second_dir, A, True)
    chosen = [first]
    root = Backend(backend_for_profile=lambda profile: chosen[0])
    store, worker, _ = service(tmp_path, backend=root, min_free_bytes=0)
    worker.start()
    try:
        request = JobRequest('fixture', settings=GenerationSettings(profile=A))
        job = worker.submit(request)
        assert wait_status(store, job.job_id, {JobStatus.COMPLETED, JobStatus.FAILED}).status is JobStatus.COMPLETED
        old = worker._resident_process
        chosen[0] = second
        job = worker.submit(JobRequest('second fixture', settings=GenerationSettings(profile=A)))
        assert wait_status(store, job.job_id, {JobStatus.COMPLETED, JobStatus.FAILED}).status is JobStatus.COMPLETED
        assert worker._resident_process.pid != old.pid
    finally:
        worker.stop()
