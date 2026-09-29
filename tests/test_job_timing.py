import json
import sqlite3

from h3server.domain import Capabilities, JobRequest, JobStatus
from h3server.store import Store


def test_timing_survives_restart_and_terminal_replay(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(time, 'time', lambda: 100.)
    store = Store(tmp_path / 'jobs.sqlite')
    job = store.accept_job(JobRequest('test'), capabilities=Capabilities())
    assert job.timing.queued_at == 100
    monkeypatch.setattr(time, 'time', lambda: 110.)
    assert store.claim_next_job().timing.started_at == 110
    monkeypatch.setattr(time, 'time', lambda: 180.)
    store.transition_job(job.job_id, JobStatus.COMPLETED)
    monkeypatch.setattr(time, 'time', lambda: 999.)
    store.transition_job(job.job_id, JobStatus.COMPLETED)
    assert Store(store.path).get_job(job.job_id).timing.finished_at == 180
    from h3server.remote.app import _job_data
    assert _job_data(store, job)['timing'] == {
        'queued_at': 100., 'started_at': 110., 'finished_at': 180., 'observed_at': 999.}


def test_legacy_history_has_no_invented_time(tmp_path):
    store = Store(tmp_path / 'jobs.sqlite')
    job = store.accept_job(JobRequest('old'), capabilities=Capabilities())
    with sqlite3.connect(store.path) as db:
        value = json.loads(db.execute('SELECT request FROM jobs').fetchone()[0])
        value.pop('_timing', None)
        db.execute('UPDATE jobs SET request=?', (json.dumps(value),))
    assert Store(store.path).get_job(job.job_id).timing is None


def test_interruption_does_not_claim_restart_was_finish_time(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(time, 'time', lambda: 100.)
    store = Store(tmp_path / 'jobs.sqlite')
    job = store.accept_job(JobRequest('test'), capabilities=Capabilities())
    store.claim_next_job()
    store.reconcile_interrupted_jobs()
    assert store.get_job(job.job_id).timing.finished_at is None
    monkeypatch.setattr(time, 'time', lambda: 200.)
    retried = store.transition_job(job.job_id, JobStatus.QUEUED)
    assert retried.timing.queued_at == 200
    assert retried.timing.started_at is None
