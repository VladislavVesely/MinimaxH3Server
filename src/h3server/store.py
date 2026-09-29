"""SQLite jobs, setup checkpoints and a transactional replay log.

Construct Store explicitly with an existing parent directory. Each operation owns
and closes its connection, so instances are safe across threads and processes.
Writes use BEGIN IMMEDIATE; reads use a consistent snapshot. No network, directory
creation, restart reconciliation or external setup action happens implicitly.
"""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from h3server.domain import Capabilities, Event, GenerationSettings, Job, JobRequest, JobStatus, StageStatus
from h3server.events import (
    MAX_EVENT_PAGE, EventPage, EventRecord, ReplayGap, _dump, _keys, _load,
    _text, decode_event, encode_event,
)


class StoreError(RuntimeError):
    """A database operation failed; nothing in its transaction was acknowledged."""


class SchemaError(StoreError):
    """Unsupported or invalid schema. The store never attempts a repair/downgrade."""


class CorruptStore(StoreError):
    """Stored bytes violate the schema or domain contracts; fail closed."""


class InvalidTransition(ValueError):
    pass


class ApprovalRequired(InvalidTransition):
    pass


class QueueFull(StoreError):
    """Acceptance exceeded the transactional pending-job bound."""


class SetupState(StrEnum):
    PLANNED = "planned"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    WAITING_FOR_REBOOT = "waiting_for_reboot"
    WAITING_FOR_REPLAN = "waiting_for_replan"
    RECONNECTING = "reconnecting"
    FAILED = "failed"
    READY = "ready"


@dataclass(frozen=True)
class StageCheckpoint:
    name: str
    status: StageStatus = StageStatus.PENDING


@dataclass(frozen=True)
class SetupCheckpoint:
    setup_id: str
    plan: dict
    plan_digest: str
    approved_digest: str | None
    state: SetupState
    stages: tuple[StageCheckpoint, ...]


@dataclass(frozen=True)
class OutputMetadata:
    """Application-selected filename, not a download path or proof a file exists."""

    filename: str
    size_bytes: int
    media_type: str
    expired: bool = False

    def __post_init__(self):
        if type(self.expired) is not bool:
            raise ValueError("expired must be bool")
        if not isinstance(self.filename, str) or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", self.filename):
            raise ValueError("output filename must be a single portable component")
        stem = self.filename.split(".", 1)[0].upper()
        if self.filename.endswith(".") or stem in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?:COM|LPT)[1-9]", stem):
            raise ValueError("reserved output filename")
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise ValueError("output size must be a nonnegative integer")
        _text(self.media_type)


_JOB_TRANSITIONS = {
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.CANCELLED, JobStatus.FAILED},
    JobStatus.RUNNING: {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED},
    JobStatus.INTERRUPTED: {JobStatus.QUEUED, JobStatus.CANCELLED},
    JobStatus.COMPLETED: set(), JobStatus.FAILED: set(), JobStatus.CANCELLED: set(),
}
_SETUP_TRANSITIONS = {
    SetupState.PLANNED: {SetupState.AWAITING_APPROVAL},
    SetupState.AWAITING_APPROVAL: {SetupState.RUNNING},
    SetupState.RUNNING: {SetupState.WAITING_FOR_REBOOT, SetupState.WAITING_FOR_REPLAN, SetupState.FAILED, SetupState.READY},
    SetupState.WAITING_FOR_REPLAN: set(),
    SetupState.WAITING_FOR_REBOOT: {SetupState.RECONNECTING, SetupState.FAILED},
    SetupState.RECONNECTING: {SetupState.RUNNING, SetupState.FAILED},
    SetupState.FAILED: {SetupState.RUNNING},
    SetupState.READY: set(),
}
_STAGE_TRANSITIONS = {
    StageStatus.PENDING: {StageStatus.RUNNING},
    StageStatus.RUNNING: {StageStatus.COMPLETED, StageStatus.FAILED},
    StageStatus.FAILED: {StageStatus.RUNNING},
    StageStatus.COMPLETED: set(),
}
_APPROVED_STATES = {SetupState.RUNNING, SetupState.WAITING_FOR_REBOOT, SetupState.WAITING_FOR_REPLAN, SetupState.RECONNECTING, SetupState.FAILED, SetupState.READY}

_SCHEMA = {
    "jobs": "CREATE TABLE jobs (job_id TEXT PRIMARY KEY NOT NULL, request TEXT NOT NULL, status TEXT NOT NULL, output TEXT)",
    "setups": "CREATE TABLE setups (setup_id TEXT PRIMARY KEY NOT NULL, checkpoint TEXT NOT NULL)",
    "events": "CREATE TABLE events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, subject_type TEXT NOT NULL, subject_id TEXT NOT NULL, payload TEXT NOT NULL)",
    "event_state": "CREATE TABLE event_state (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), last_id INTEGER NOT NULL, minimum_cursor INTEGER NOT NULL)",
}
_SCHEMA_VERSION = 1


def _bounded_int(value: int, minimum: int, maximum: int | None = None) -> None:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError("integer outside supported bounds")


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("invalid server-generated identifier")
    return value


def canonical_plan_digest(plan: dict) -> str:
    """SHA-256 over strict sorted JSON, including the ordered unique stages list.

    Arbitrary plan details (including separate approval scopes) are retained.
    Approval here authorizes this exact document, not implicit sudo/reboot rights.
    """
    encoded = _dump(plan)
    if type(plan) is not dict or type(plan.get("stages")) is not list:
        raise ValueError("plan must contain an ordered stages list")
    for name in plan["stages"]:
        _text(name)
    if len(set(plan["stages"])) != len(plan["stages"]):
        raise ValueError("stage names must be unique")
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _request_data(request: JobRequest) -> dict:
    if not isinstance(request, JobRequest):
        raise ValueError("expected JobRequest")
    return {"prompt": request.prompt, "loras": list(request.loras), "sparse_attention": request.sparse_attention,
            "settings": asdict(request.settings) if request.settings is not None else None}


def _decode_job(row: sqlite3.Row) -> tuple[Job, OutputMetadata | None]:
    request = _load(row["request"])
    # Additive JSON migration: v1 rows remain readable without rewriting history.
    # Legacy jobs have no executable settings and must not run with guessed ones.
    if type(request) is dict and "settings" not in request:
        request["settings"] = None
    request = _keys(request, {"prompt", "loras", "sparse_attention", "settings"})
    if request["settings"] is not None:
        settings = _keys(request["settings"], {"num_frames", "width", "height", "steps", "seed", "profile"})
        request["settings"] = GenerationSettings(**settings)
    _text(request["prompt"])
    if type(request["loras"]) is not list:
        raise ValueError("invalid LoRA list")
    request["loras"] = tuple(request["loras"])
    job = Job(_identifier(row["job_id"]), JobRequest(**request), JobStatus(row["status"]))
    output = None
    if row["output"] is not None:
        data = _load(row["output"])
        if type(data) is dict and "expired" not in data:
            data["expired"] = False
        data = _keys(data, {"filename", "size_bytes", "media_type", "expired"})
        output = OutputMetadata(**data)
        if job.status is not JobStatus.COMPLETED:
            raise ValueError("only completed jobs may have output metadata")
    return job, output


def _checkpoint_data(checkpoint: SetupCheckpoint) -> dict:
    return {"plan": checkpoint.plan, "plan_digest": checkpoint.plan_digest,
            "approved_digest": checkpoint.approved_digest, "state": checkpoint.state.value,
            "stages": [{"name": stage.name, "status": stage.status.value} for stage in checkpoint.stages]}


def _decode_setup(row: sqlite3.Row) -> SetupCheckpoint:
    data = _keys(_load(row["checkpoint"]), {"plan", "plan_digest", "approved_digest", "state", "stages"})
    if canonical_plan_digest(data["plan"]) != data["plan_digest"]:
        raise ValueError("plan digest mismatch")
    if data["approved_digest"] not in (None, data["plan_digest"]):
        raise ValueError("stale stored approval")
    state = SetupState(data["state"])
    if state in _APPROVED_STATES and data["approved_digest"] is None:
        raise ValueError("unapproved execution state")
    if state is SetupState.PLANNED and data["approved_digest"] is not None:
        raise ValueError("planned state cannot have approval")
    if type(data["stages"]) is not list:
        raise ValueError("invalid stages")
    stages = []
    unfinished = False
    for item in data["stages"]:
        item = _keys(item, {"name", "status"})
        status = StageStatus(item["status"])
        if status not in _STAGE_TRANSITIONS or (unfinished and status is not StageStatus.PENDING):
            raise ValueError("invalid stage sequence")
        unfinished |= status is not StageStatus.COMPLETED
        stages.append(StageCheckpoint(_text(item["name"]), status))
    if [stage.name for stage in stages] != data["plan"]["stages"]:
        raise ValueError("checkpoint stages differ from plan")
    if state in {SetupState.PLANNED, SetupState.AWAITING_APPROVAL} and any(stage.status is not StageStatus.PENDING for stage in stages):
        raise ValueError("stages started before approval")
    if state in {SetupState.READY, SetupState.WAITING_FOR_REPLAN} and unfinished:
        raise ValueError("finished setup round has incomplete stages")
    return SetupCheckpoint(_identifier(row["setup_id"]), data["plan"], data["plan_digest"], data["approved_digest"], state, tuple(stages))


def _decode_record(row: sqlite3.Row) -> EventRecord:
    _bounded_int(row["event_id"], 1)
    return EventRecord(row["event_id"], _text(row["subject_type"]), _text(row["subject_id"]), decode_event(row["payload"]))


def _stored(decoder, row):
    try:
        return decoder(row)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        raise CorruptStore("stored record violates its domain contract") from error


class Store:
    def __init__(self, path: str | Path, *, event_retention: int = 10000):
        _bounded_int(event_retention, 1)
        self.path = Path(path).absolute()
        self.event_retention = event_retention
        with self._transaction(write=True) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            objects = db.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'").fetchall()
            tables = {row["name"]: row["sql"] for row in objects if row["type"] == "table"}
            if len(tables) != len(objects):
                raise SchemaError("unexpected schema objects")
            if version == 0 and not objects:
                for sql in _SCHEMA.values():
                    db.execute(sql)
                db.execute("INSERT INTO event_state VALUES (1, 0, 0)")
                db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            elif version != _SCHEMA_VERSION or tables != _SCHEMA:
                raise SchemaError("unsupported schema version or invalid schema")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise CorruptStore("SQLite integrity check failed")
            self._validate_records(db)

    @contextmanager
    def _transaction(self, *, write: bool = False):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except sqlite3.DatabaseError as error:
            code = getattr(error, "sqlite_errorcode", 0) & 0xFF
            kind = CorruptStore if code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB) else StoreError
            raise kind("SQLite operation failed") from error
        finally:
            if db is not None:
                db.close()  # rolls back any uncommitted transaction, including validation failures

    def _event_bounds(self, db) -> tuple[int, int]:
        rows = db.execute("SELECT * FROM event_state").fetchall()
        if len(rows) != 1 or rows[0]["singleton"] != 1:
            raise CorruptStore("invalid event replay metadata")
        last, floor = rows[0]["last_id"], rows[0]["minimum_cursor"]
        if type(last) is not int or type(floor) is not int or not 0 <= floor <= last:
            raise CorruptStore("invalid event replay bounds")
        return last, floor

    def _validate_records(self, db):
        for row in db.execute("SELECT * FROM jobs"):
            _stored(_decode_job, row)
        for row in db.execute("SELECT * FROM setups"):
            _stored(_decode_setup, row)
        last, floor = self._event_bounds(db)
        expected = floor + 1
        for row in db.execute("SELECT * FROM events ORDER BY event_id"):
            record = _stored(_decode_record, row)
            if record.event_id != expected:
                raise CorruptStore("event log contains a replay hole")
            expected += 1
        if expected != last + 1:
            raise CorruptStore("event log tail is missing")
        sequence = db.execute("SELECT seq FROM sqlite_sequence WHERE name='events'").fetchall()
        if (not sequence and last != 0) or (sequence and (len(sequence) != 1 or sequence[0][0] != last)):
            raise CorruptStore("invalid event sequence")

    def _append(self, db, subject_type: str, subject_id: str, event: Event) -> EventRecord:
        _text(subject_type)
        _text(subject_id)
        payload = encode_event(event)
        last, floor = self._event_bounds(db)
        cursor = db.execute("INSERT INTO events(subject_type, subject_id, payload) VALUES (?, ?, ?)", (subject_type, subject_id, payload))
        event_id = cursor.lastrowid
        if event_id != last + 1:
            raise CorruptStore("event sequence mismatch")
        floor = max(floor, event_id - self.event_retention)
        db.execute("DELETE FROM events WHERE event_id <= ?", (floor,))
        db.execute("UPDATE event_state SET last_id=?, minimum_cursor=? WHERE singleton=1", (event_id, floor))
        return EventRecord(event_id, subject_type, subject_id, decode_event(payload))

    def append_event(self, subject_type: str, subject_id: str, event: Event) -> EventRecord:
        """Append a log/progress event; lifecycle methods append their own atomically."""
        with self._transaction(write=True) as db:
            return self._append(db, subject_type, subject_id, event)

    def read_events(self, *, after: int = 0, limit: int = 100) -> EventPage:
        """Global exclusive cursor. Retained history never silently hides a gap.

        Pages are bounded to MAX_EVENT_PAGE. On ReplayGap refresh durable state,
        then replay from the supplied minimum_cursor; future cursors are errors.
        """
        _bounded_int(after, 0)
        _bounded_int(limit, 1, MAX_EVENT_PAGE)
        with self._transaction() as db:
            last, floor = self._event_bounds(db)
            if after < floor:
                raise ReplayGap(after, floor)
            if after > last:
                raise ValueError("cursor is ahead of the event log")
            rows = db.execute("SELECT * FROM events WHERE event_id > ? ORDER BY event_id LIMIT ?", (after, limit + 1)).fetchall()
            records = tuple(_stored(_decode_record, row) for row in rows)
            if [record.event_id for record in records] != list(range(after + 1, min(last, after + limit + 1) + 1)):
                raise CorruptStore("event log contains a replay hole")
            page = records[:limit]
            return EventPage(page, page[-1].event_id if page else after, len(records) > limit)

    def accept_job(self, request: JobRequest, *, capabilities: Capabilities, pending_limit: int | None = None) -> Job:
        request.validate_capabilities(capabilities)
        payload = _dump(_request_data(request))
        job = Job(uuid4().hex, request)
        if pending_limit is not None:
            _bounded_int(pending_limit, 1)
        with self._transaction(write=True) as db:
            if pending_limit is not None:
                pending = db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued', 'running')").fetchone()[0]
                if pending >= pending_limit:
                    raise QueueFull("pending job limit reached")
            db.execute("INSERT INTO jobs VALUES (?, ?, ?, NULL)", (job.job_id, payload, job.status.value))
            _stored(_decode_job, db.execute("SELECT * FROM jobs WHERE job_id=?", (job.job_id,)).fetchone())
            self._append(db, "job", job.job_id, Event("queued", StageStatus.PENDING))
        return job

    def _job(self, db, job_id):
        row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _stored(_decode_job, row)

    def get_job(self, job_id: str) -> Job:
        with self._transaction() as db:
            return self._job(db, job_id)[0]

    def get_job_output(self, job_id: str) -> OutputMetadata | None:
        with self._transaction() as db:
            return self._job(db, job_id)[1]

    def get_job_snapshot(self, job_id: str) -> tuple[Job, OutputMetadata | None]:
        """Read lifecycle and output from one snapshot for API state refreshes."""
        with self._transaction() as db:
            return self._job(db, job_id)

    def list_jobs(self, *, limit: int = 100, offset: int = 0) -> tuple[Job, ...]:
        _bounded_int(limit, 1, MAX_EVENT_PAGE)
        _bounded_int(offset, 0)
        with self._transaction() as db:
            return tuple(_stored(_decode_job, row)[0] for row in db.execute("SELECT * FROM jobs ORDER BY rowid DESC LIMIT ? OFFSET ?", (limit, offset)))

    def _transition_job(self, db, job_id, target, output):
        job, existing_output = self._job(db, job_id)
        if output is not None and target is not JobStatus.COMPLETED:
            raise InvalidTransition("output metadata requires completed state")
        if job.status is target:
            if output is not None and output != existing_output:
                raise InvalidTransition("cannot rewrite terminal output metadata")
            return job
        if target not in _JOB_TRANSITIONS[job.status]:
            raise InvalidTransition(f"job cannot move from {job.status} to {target}")
        if output is not None and not isinstance(output, OutputMetadata):
            raise ValueError("expected OutputMetadata")
        payload = _dump(asdict(output)) if output is not None else None
        db.execute("UPDATE jobs SET status=?, output=? WHERE job_id=?", (target.value, payload, job_id))
        stage_status = {JobStatus.QUEUED: StageStatus.PENDING, JobStatus.RUNNING: StageStatus.RUNNING,
                        JobStatus.COMPLETED: StageStatus.COMPLETED, JobStatus.CANCELLED: StageStatus.CANCELLED,
                        JobStatus.FAILED: StageStatus.FAILED, JobStatus.INTERRUPTED: StageStatus.FAILED}[target]
        self._append(db, "job", job_id, Event(target.value, stage_status))
        return Job(job_id, job.request, target)

    def transition_job(self, job_id: str, target: JobStatus, *, output: OutputMetadata | None = None) -> Job:
        with self._transaction(write=True) as db:
            return self._transition_job(db, job_id, JobStatus(target), output)

    def claim_next_job(self) -> Job | None:
        """Atomically claim FIFO work, never while another persisted job runs."""
        with self._transaction(write=True) as db:
            if db.execute("SELECT 1 FROM jobs WHERE status='running' LIMIT 1").fetchone():
                return None
            row = db.execute("SELECT job_id FROM jobs WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            return self._transition_job(db, row[0], JobStatus.RUNNING, None) if row else None

    def expire_job_output(self, job_id: str) -> None:
        """Preserve completion history, but durably revoke output availability."""
        with self._transaction(write=True) as db:
            job, output = self._job(db, job_id)
            if job.status is not JobStatus.COMPLETED or output is None or output.expired:
                return
            data = asdict(output)
            data["expired"] = True
            db.execute("UPDATE jobs SET output=? WHERE job_id=?", (_dump(data), job_id))
            self._append(db, "job", job_id, Event("output_expired", StageStatus.COMPLETED))

    def reconcile_interrupted_jobs(self) -> tuple[str, ...]:
        """Call only after establishing exclusive daemon/worker ownership at restart."""
        with self._transaction(write=True) as db:
            ids = tuple(row[0] for row in db.execute("SELECT job_id FROM jobs WHERE status=? ORDER BY rowid", (JobStatus.RUNNING.value,)))
            for job_id in ids:
                self._transition_job(db, job_id, JobStatus.INTERRUPTED, None)
            return ids

    def _setup(self, db, setup_id):
        row = db.execute("SELECT * FROM setups WHERE setup_id=?", (setup_id,)).fetchone()
        if row is None:
            raise KeyError(setup_id)
        return _stored(_decode_setup, row)

    def get_setup(self, setup_id: str) -> SetupCheckpoint:
        with self._transaction() as db:
            return self._setup(db, setup_id)

    def list_setups(self, *, limit: int = 100, offset: int = 0) -> tuple[SetupCheckpoint, ...]:
        """Discover resumable checkpoints after the controller loses in-memory IDs."""
        _bounded_int(limit, 1, MAX_EVENT_PAGE)
        _bounded_int(offset, 0)
        with self._transaction() as db:
            return tuple(_stored(_decode_setup, row) for row in db.execute("SELECT * FROM setups ORDER BY rowid DESC LIMIT ? OFFSET ?", (limit, offset)))

    def _save_setup(self, db, setup: SetupCheckpoint, event: Event) -> SetupCheckpoint:
        payload = _dump(_checkpoint_data(setup))
        db.execute("INSERT INTO setups VALUES (?, ?) ON CONFLICT(setup_id) DO UPDATE SET checkpoint=excluded.checkpoint", (setup.setup_id, payload))
        result = self._setup(db, setup.setup_id)
        self._append(db, "setup", setup.setup_id, event)
        return result

    def create_setup(self, plan: dict) -> SetupCheckpoint:
        digest = canonical_plan_digest(plan)
        setup = SetupCheckpoint(uuid4().hex, plan, digest, None, SetupState.PLANNED,
                                tuple(StageCheckpoint(name) for name in plan["stages"]))
        with self._transaction(write=True) as db:
            return self._save_setup(db, setup, Event("planned", StageStatus.PENDING))

    def update_setup_plan(self, setup_id: str, plan: dict) -> SetupCheckpoint:
        """Replacing an inactive plan invalidates approval and all stage checkpoints."""
        digest = canonical_plan_digest(plan)
        with self._transaction(write=True) as db:
            existing = self._setup(db, setup_id)
            if existing.state in {SetupState.RUNNING, SetupState.WAITING_FOR_REBOOT, SetupState.RECONNECTING}:
                raise InvalidTransition("cannot replace an active plan")
            if existing.plan_digest == digest:
                return existing
            setup = SetupCheckpoint(setup_id, plan, digest, None, SetupState.PLANNED,
                                    tuple(StageCheckpoint(name) for name in plan["stages"]))
            return self._save_setup(db, setup, Event("planned", StageStatus.PENDING, "Plan changed; approval invalidated"))

    def approve_setup(self, setup_id: str, plan_digest: str) -> SetupCheckpoint:
        with self._transaction(write=True) as db:
            setup = self._setup(db, setup_id)
            if setup.state is not SetupState.AWAITING_APPROVAL:
                raise InvalidTransition("setup is not awaiting approval")
            if setup.plan_digest != plan_digest:
                raise ApprovalRequired("approval refers to a stale plan")
            if setup.approved_digest == plan_digest:
                return setup
            approved = SetupCheckpoint(setup_id, setup.plan, setup.plan_digest, plan_digest, setup.state, setup.stages)
            return self._save_setup(db, approved, Event("approval", StageStatus.COMPLETED))

    def transition_setup(self, setup_id: str, target: SetupState) -> SetupCheckpoint:
        target = SetupState(target)
        with self._transaction(write=True) as db:
            setup = self._setup(db, setup_id)
            if setup.state is target:
                return setup
            if target not in _SETUP_TRANSITIONS[setup.state]:
                raise InvalidTransition(f"setup cannot move from {setup.state} to {target}")
            if target in _APPROVED_STATES and setup.approved_digest != setup.plan_digest:
                raise ApprovalRequired("execution requires approval of the current plan")
            if target in {SetupState.READY, SetupState.WAITING_FOR_REPLAN} and any(stage.status is not StageStatus.COMPLETED for stage in setup.stages):
                raise InvalidTransition("all stages must complete before readiness or replanning")
            updated = SetupCheckpoint(setup_id, setup.plan, setup.plan_digest, setup.approved_digest, target, setup.stages)
            status = StageStatus.COMPLETED if target is SetupState.READY else StageStatus.FAILED if target is SetupState.FAILED else StageStatus.RUNNING if target is SetupState.RUNNING else StageStatus.PENDING
            return self._save_setup(db, updated, Event(target.value, status))

    def transition_stage(self, setup_id: str, name: str, target: StageStatus) -> SetupCheckpoint:
        """Persist ordered stage progress; identical transitions are no-ops.

        Running survives restart so the executor can retry an idempotent action.
        This checkpoint is not an execution lease or an exactly-once IO promise.
        """
        target = StageStatus(target)
        with self._transaction(write=True) as db:
            setup = self._setup(db, setup_id)
            stages = list(setup.stages)
            index = next((i for i, stage in enumerate(stages) if stage.name == name), None)
            if index is None:
                raise KeyError(name)
            if stages[index].status is target:
                return setup
            if setup.state is not SetupState.RUNNING:
                raise InvalidTransition("stages require a running setup")
            if target not in _STAGE_TRANSITIONS[stages[index].status]:
                raise InvalidTransition("invalid stage transition")
            if any(stage.status is not StageStatus.COMPLETED for stage in stages[:index]):
                raise InvalidTransition("prior stages are incomplete")
            stages[index] = StageCheckpoint(name, target)
            updated = SetupCheckpoint(setup_id, setup.plan, setup.plan_digest, setup.approved_digest, setup.state, tuple(stages))
            return self._save_setup(db, updated, Event(name, target))
