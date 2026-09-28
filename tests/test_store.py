"""Durable state contracts, using real SQLite and process boundaries."""

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import multiprocessing
import sqlite3
from contextlib import closing

import pytest

from h3server.domain import Capabilities, JobRequest, JobStatus, StageStatus


def store_at(tmp_path, **kwargs):
    from h3server.store import Store
    return Store(tmp_path / "state.db", **kwargs)


def submit_in_process(path):
    from h3server.store import Store
    return Store(path).accept_job(JobRequest("process"), capabilities=Capabilities()).job_id


def test_accept_reopen_and_opaque_ids(tmp_path):
    from h3server.paths import AppPaths
    store = store_at(tmp_path)
    job = store.accept_job(JobRequest("landscape"), capabilities=Capabilities())
    assert job.status is JobStatus.QUEUED
    assert job.job_id != "landscape"
    assert AppPaths(tmp_path).job_dir(job.job_id).name == job.job_id
    reopened = store_at(tmp_path)
    assert reopened.get_job(job.job_id) == job
    assert reopened.list_jobs() == (job,)
    assert reopened.read_events().events[0].subject_id == job.job_id
    with pytest.raises(KeyError):
        reopened.get_job("missing")


def test_accept_validates_capabilities_and_rolls_back_event_failure(tmp_path):
    from h3server.store import StoreError
    store = store_at(tmp_path)
    with pytest.raises(ValueError):
        store.accept_job(JobRequest("x", sparse_attention=False), capabilities=Capabilities())
    with closing(sqlite3.connect(tmp_path / "state.db", isolation_level=None)) as db:
        db.execute("CREATE TRIGGER reject_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'test'); END")
    with pytest.raises(StoreError):
        store.accept_job(JobRequest("x"), capabilities=Capabilities())
    assert store.list_jobs() == ()
    assert store.read_events().events == ()


def test_thread_and_spawn_process_writers(tmp_path):
    store = store_at(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: store.accept_job(JobRequest("thread"), capabilities=Capabilities()).job_id, range(12)))
    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as pool:
        ids += list(pool.map(submit_in_process, [str(tmp_path / "state.db")] * 6))
    assert len(set(ids)) == 18
    assert len(store.list_jobs()) == 18
    events = store.read_events().events
    assert len(events) == 18
    assert [event.event_id for event in events] == list(range(1, 19))


def test_job_transitions_output_and_explicit_restart_reconciliation(tmp_path):
    from h3server.store import InvalidTransition, OutputMetadata
    store = store_at(tmp_path)
    job = store.accept_job(JobRequest("x"), capabilities=Capabilities())
    with pytest.raises(InvalidTransition):
        store.transition_job(job.job_id, JobStatus.COMPLETED)
    store.transition_job(job.job_id, JobStatus.RUNNING)
    assert store_at(tmp_path).get_job(job.job_id).status is JobStatus.RUNNING
    assert store.reconcile_interrupted_jobs() == (job.job_id,)
    assert store.reconcile_interrupted_jobs() == ()
    assert store.get_job(job.job_id).status is JobStatus.INTERRUPTED
    with pytest.raises(InvalidTransition):
        store.transition_job(job.job_id, JobStatus.COMPLETED)
    store.transition_job(job.job_id, JobStatus.QUEUED)
    store.transition_job(job.job_id, JobStatus.RUNNING)
    output = OutputMetadata("video.mp4", 123, "video/mp4")
    store.transition_job(job.job_id, JobStatus.COMPLETED, output=output)
    assert store_at(tmp_path).get_job_output(job.job_id) == output
    assert store.get_job(job.job_id).status is JobStatus.COMPLETED
    with pytest.raises(InvalidTransition):
        store.transition_job(job.job_id, JobStatus.RUNNING)


@pytest.mark.parametrize("target", [JobStatus.CANCELLED, JobStatus.FAILED])
def test_job_terminal_transitions(tmp_path, target):
    store = store_at(tmp_path)
    job = store.accept_job(JobRequest("x"), capabilities=Capabilities())
    store.transition_job(job.job_id, target)
    count = len(store.read_events().events)
    store.transition_job(job.job_id, target)
    assert len(store.read_events().events) == count


@pytest.mark.parametrize("filename,size", [("../x", 1), ("/x", 1), ("x", -1), ("x", True)])
def test_output_metadata_rejects_unsafe_values(filename, size):
    from h3server.store import OutputMetadata
    with pytest.raises(ValueError):
        OutputMetadata(filename, size, "video/mp4")


def plan():
    return {"stages": ["environment", "model"], "changes": {"sudo": False, "model": "pinned"}}


def approved_setup(store):
    from h3server.store import SetupState
    setup = store.create_setup(plan())
    store.transition_setup(setup.setup_id, SetupState.AWAITING_APPROVAL)
    store.approve_setup(setup.setup_id, setup.plan_digest)
    return store.transition_setup(setup.setup_id, SetupState.RUNNING)


def test_setup_canonical_digest_and_stale_approval(tmp_path):
    from h3server.store import ApprovalRequired, SetupState, canonical_plan_digest
    store = store_at(tmp_path)
    setup = store.create_setup(plan())
    assert setup.state is SetupState.PLANNED
    assert canonical_plan_digest({"changes": {"model": "pinned", "sudo": False}, "stages": ["environment", "model"]}) == setup.plan_digest
    store.transition_setup(setup.setup_id, SetupState.AWAITING_APPROVAL)
    with pytest.raises(ApprovalRequired):
        store.transition_setup(setup.setup_id, SetupState.RUNNING)
    with pytest.raises(ApprovalRequired):
        store.approve_setup(setup.setup_id, "stale")
    store.approve_setup(setup.setup_id, setup.plan_digest)
    assert store_at(tmp_path).get_setup(setup.setup_id).approved_digest == setup.plan_digest
    modified = plan()
    modified["changes"]["sudo"] = True
    updated = store.update_setup_plan(setup.setup_id, modified)
    assert updated.state is SetupState.PLANNED
    assert updated.approved_digest is None
    assert updated.plan_digest != setup.plan_digest
    store.transition_setup(setup.setup_id, SetupState.AWAITING_APPROVAL)
    with pytest.raises(ApprovalRequired):
        store.approve_setup(setup.setup_id, setup.plan_digest)


def test_setup_stages_reopen_reboot_resume_and_idempotence(tmp_path):
    from h3server.store import InvalidTransition, SetupState
    store = store_at(tmp_path)
    setup = approved_setup(store)
    with pytest.raises(InvalidTransition):
        store.transition_stage(setup.setup_id, "model", StageStatus.RUNNING)
    with pytest.raises(InvalidTransition):
        store.transition_stage(setup.setup_id, "environment", StageStatus.COMPLETED)
    store.transition_stage(setup.setup_id, "environment", StageStatus.RUNNING)
    store.transition_stage(setup.setup_id, "environment", StageStatus.COMPLETED)
    count = len(store.read_events().events)
    store.transition_stage(setup.setup_id, "environment", StageStatus.COMPLETED)
    assert len(store.read_events().events) == count
    store.transition_setup(setup.setup_id, SetupState.WAITING_FOR_REBOOT)
    store = store_at(tmp_path)
    assert store.get_setup(setup.setup_id).stages[0].status is StageStatus.COMPLETED
    store.transition_setup(setup.setup_id, SetupState.RECONNECTING)
    store.transition_setup(setup.setup_id, SetupState.RUNNING)
    with pytest.raises(InvalidTransition):
        store.transition_setup(setup.setup_id, SetupState.READY)
    store.transition_stage(setup.setup_id, "model", StageStatus.RUNNING)
    store.transition_stage(setup.setup_id, "model", StageStatus.FAILED)
    store.transition_setup(setup.setup_id, SetupState.FAILED)
    store.transition_setup(setup.setup_id, SetupState.RUNNING)
    store.transition_stage(setup.setup_id, "model", StageStatus.RUNNING)
    store.transition_stage(setup.setup_id, "model", StageStatus.COMPLETED)
    assert store.transition_setup(setup.setup_id, SetupState.READY).state is SetupState.READY


def test_setup_rejects_unknown_states_and_active_plan_mutation(tmp_path):
    from h3server.store import InvalidTransition, SetupState
    store = store_at(tmp_path)
    setup = approved_setup(store)
    with pytest.raises(ValueError):
        store.transition_setup(setup.setup_id, "arbitrary")
    with pytest.raises(InvalidTransition):
        store.transition_setup(setup.setup_id, SetupState.PLANNED)
    with pytest.raises(InvalidTransition):
        store.update_setup_plan(setup.setup_id, plan())
    with pytest.raises(KeyError):
        store.transition_stage(setup.setup_id, "unknown", StageStatus.RUNNING)


@pytest.mark.parametrize("bad_plan", [{}, {"stages": ["a", "a"]}, {"stages": "x"}, {"stages": [""]}, {"stages": [], "x": float("nan")}, {"stages": [], 1: "x"}])
def test_plan_rejects_invalid_json_or_stage_definitions(tmp_path, bad_plan):
    store = store_at(tmp_path)
    with pytest.raises(ValueError):
        store.create_setup(bad_plan)


@pytest.mark.parametrize("mutation", [
    "UPDATE setups SET checkpoint='not json'",
    "UPDATE setups SET checkpoint=json_set(checkpoint, '$.state', 'invented')",
    "UPDATE setups SET checkpoint=json_set(checkpoint, '$.plan.changes.sudo', 1)",
    "UPDATE setups SET checkpoint=json_set(checkpoint, '$.approved_digest', 'stale')",
    "UPDATE setups SET checkpoint=json_set(checkpoint, '$.stages[0].status', 'invented')",
    "UPDATE setups SET checkpoint=json_set(checkpoint, '$.state', 'ready')",
])
def test_checkpoint_corruption_fails_closed(tmp_path, mutation):
    from h3server.store import CorruptStore
    store = store_at(tmp_path)
    setup = approved_setup(store)
    with closing(sqlite3.connect(tmp_path / "state.db", isolation_level=None)) as db:
        db.execute(mutation)
    with pytest.raises(CorruptStore):
        store.get_setup(setup.setup_id)
    with pytest.raises(CorruptStore):
        store_at(tmp_path)


@pytest.mark.parametrize("mutation", [
    "UPDATE jobs SET status='invented'",
    "UPDATE jobs SET request='{}'",
    "UPDATE jobs SET request='{\"prompt\":\"x\",\"loras\":[],\"sparse_attention\":1}'",
    "UPDATE jobs SET output='{}'",
])
def test_job_corruption_fails_closed(tmp_path, mutation):
    from h3server.store import CorruptStore
    store = store_at(tmp_path)
    job = store.accept_job(JobRequest("x"), capabilities=Capabilities())
    with closing(sqlite3.connect(tmp_path / "state.db", isolation_level=None)) as db:
        db.execute(mutation)
    with pytest.raises(CorruptStore):
        store.get_job(job.job_id)
    with pytest.raises(CorruptStore):
        store_at(tmp_path)


@pytest.mark.parametrize("sql", ["PRAGMA user_version=999", "PRAGMA user_version=0", "DROP TABLE events"])
def test_rejects_newer_and_invalid_schema_without_reinitializing(tmp_path, sql):
    from h3server.store import SchemaError
    store_at(tmp_path)
    with closing(sqlite3.connect(tmp_path / "state.db", isolation_level=None)) as db:
        db.execute(sql)
    before = (tmp_path / "state.db").read_bytes()
    with pytest.raises(SchemaError):
        store_at(tmp_path)
    assert (tmp_path / "state.db").read_bytes() == before


def test_rejects_non_database_and_does_not_create_parent(tmp_path):
    from h3server.store import CorruptStore, Store, StoreError
    path = tmp_path / "bad.db"
    path.write_bytes(b"not a database")
    with pytest.raises(CorruptStore):
        Store(path)
    with pytest.raises(StoreError):
        Store(tmp_path / "missing" / "state.db")
    assert not (tmp_path / "missing").exists()


def test_setup_discovery_after_reopen_and_snapshot_isolation(tmp_path):
    store = store_at(tmp_path)
    original_plan = plan()
    first = store.create_setup(original_plan)
    second = store.create_setup(plan())
    original_plan["changes"]["sudo"] = True
    first.plan["stages"].append("not persisted")
    reopened = store_at(tmp_path)
    assert [item.setup_id for item in reopened.list_setups(limit=1)] == [second.setup_id]
    assert reopened.list_setups(limit=1, offset=1)[0].plan == plan()
    assert reopened.get_setup(first.setup_id).plan == plan()


@pytest.mark.parametrize("sql", [
    "CREATE VIEW unexpected AS SELECT 1",
    "CREATE TABLE unrelated (value)",
])
def test_does_not_initialize_nonempty_unversioned_database(tmp_path, sql):
    from h3server.store import SchemaError
    with closing(sqlite3.connect(tmp_path / "state.db", isolation_level=None)) as db:
        db.execute(sql)
    before = (tmp_path / "state.db").read_bytes()
    with pytest.raises(SchemaError):
        store_at(tmp_path)
    assert (tmp_path / "state.db").read_bytes() == before


def test_rejects_unexpected_trigger_in_versioned_schema(tmp_path):
    from h3server.store import SchemaError
    store_at(tmp_path)
    with closing(sqlite3.connect(tmp_path / "state.db", isolation_level=None)) as db:
        db.execute("CREATE TRIGGER unexpected AFTER INSERT ON jobs BEGIN DELETE FROM events; END")
    with pytest.raises(SchemaError):
        store_at(tmp_path)


def test_job_transition_event_failure_rolls_back_status_output_and_retention(tmp_path):
    from h3server.store import OutputMetadata, StoreError
    store = store_at(tmp_path, event_retention=1)
    job = store.accept_job(JobRequest("x"), capabilities=Capabilities())
    store.transition_job(job.job_id, JobStatus.RUNNING)
    before = store.read_events(after=1)
    with closing(sqlite3.connect(tmp_path / "state.db", isolation_level=None)) as db:
        db.execute("CREATE TRIGGER reject_retention BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'test'); END")
    with pytest.raises(StoreError):
        store.transition_job(job.job_id, JobStatus.COMPLETED, output=OutputMetadata("video.mp4", 1, "video/mp4"))
    assert store.get_job(job.job_id).status is JobStatus.RUNNING
    assert store.get_job_output(job.job_id) is None
    assert store.read_events(after=1) == before
    with closing(sqlite3.connect(tmp_path / "state.db", isolation_level=None)) as db:
        db.execute("DROP TRIGGER reject_retention")
    store.transition_job(job.job_id, JobStatus.FAILED)
    assert store.read_events(after=2).next_cursor == 3


def test_setup_checkpoint_and_event_transaction_rollback(tmp_path):
    from h3server.store import StoreError
    store = store_at(tmp_path)
    setup = approved_setup(store)
    before = store.read_events()
    with closing(sqlite3.connect(tmp_path / "state.db", isolation_level=None)) as db:
        db.execute("CREATE TRIGGER reject_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'test'); END")
    with pytest.raises(StoreError):
        store.transition_stage(setup.setup_id, "environment", StageStatus.RUNNING)
    assert store.get_setup(setup.setup_id) == setup
    assert store.read_events() == before


def test_competing_terminal_transitions_commit_one_winner(tmp_path):
    from h3server.store import InvalidTransition
    store = store_at(tmp_path)
    job = store.accept_job(JobRequest("x"), capabilities=Capabilities())
    store.transition_job(job.job_id, JobStatus.RUNNING)

    def attempt(target):
        try:
            return store.transition_job(job.job_id, target).status
        except InvalidTransition:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [JobStatus.COMPLETED, JobStatus.CANCELLED]))
    assert results.count(None) == 1
    assert store.get_job(job.job_id).status in results
    assert len(store.read_events().events) == 3


def test_import_does_not_create_application_directories(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = {**os.environ, "HOME": str(tmp_path), "PYTHONPATH": str(Path("src").absolute())}
    result = subprocess.run([sys.executable, "-B", "-c", "import h3server.store, h3server.events"],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_does_not_ignore_nonreserved_sqlite_named_tables(tmp_path):
    from h3server.store import SchemaError
    store_at(tmp_path)
    with closing(sqlite3.connect(tmp_path / "state.db", isolation_level=None)) as db:
        db.execute("CREATE TABLE sqliteextra (value)")
    with pytest.raises(SchemaError):
        store_at(tmp_path)


def test_concurrent_first_open_initializes_schema_once(tmp_path):
    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as pool:
        ids = list(pool.map(submit_in_process, [str(tmp_path / "state.db")] * 4))
    store = store_at(tmp_path)
    assert len(set(ids)) == len(store.list_jobs()) == 4
    assert [item.event_id for item in store.read_events().events] == [1, 2, 3, 4]


def test_job_request_options_survive_json_round_trip(tmp_path):
    from h3server.domain import Capability, CapabilityStatus
    supported = Capability(CapabilityStatus.SUPPORTED, ("verified fixture",))
    capabilities = Capabilities(lora=supported, sparse_attention=supported)
    store = store_at(tmp_path)
    request = JobRequest("海岸 🌊", loras=("one", "two"), sparse_attention=False)
    job = store.accept_job(request, capabilities=capabilities)
    assert store_at(tmp_path).get_job(job.job_id).request == request


def test_repeated_approval_and_unchanged_plan_preserve_checkpoint(tmp_path):
    from h3server.store import SetupState
    store = store_at(tmp_path)
    setup = store.create_setup(plan())
    store.transition_setup(setup.setup_id, SetupState.AWAITING_APPROVAL)
    approved = store.approve_setup(setup.setup_id, setup.plan_digest)
    count = len(store.read_events().events)
    assert store.approve_setup(setup.setup_id, setup.plan_digest) == approved
    assert store.update_setup_plan(setup.setup_id, plan()) == approved
    assert len(store.read_events().events) == count
