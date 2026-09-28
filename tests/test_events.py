"""Ordered bounded replay and strict domain event serialization."""

import sqlite3
from contextlib import closing

import pytest

from h3server.domain import Event, Progress, StageStatus


def test_domain_event_round_trip_and_cursors(tmp_path):
    from h3server.store import Store
    store = Store(tmp_path / "state.db")
    events = [Event("download", StageStatus.RUNNING, "bytes", Progress(3, "bytes", 10)),
              Event("export", StageStatus.RUNNING), Event("export", StageStatus.COMPLETED)]
    records = [store.append_event("worker", "worker-1", event) for event in events]
    assert [record.event for record in records] == events
    reopened = Store(tmp_path / "state.db")
    page = reopened.read_events(limit=2)
    assert page.events == tuple(records[:2])
    assert page.next_cursor == records[1].event_id
    assert page.has_more is True
    last = reopened.read_events(after=page.next_cursor, limit=2)
    assert last.events == (records[2],)
    assert last.next_cursor == records[2].event_id
    assert last.has_more is False
    empty = reopened.read_events(after=last.next_cursor)
    assert empty.events == ()
    assert empty.next_cursor == last.next_cursor
    assert records[0].event.progress.percent == 30
    assert records[1].event.progress is None


def test_retention_explicit_gap_and_ids_never_reused(tmp_path):
    from h3server.events import ReplayGap
    from h3server.store import Store
    store = Store(tmp_path / "state.db", event_retention=2)
    for _ in range(5):
        store.append_event("worker", "w", Event("scan", StageStatus.RUNNING))
    with pytest.raises(ReplayGap) as failure:
        store.read_events(after=0)
    assert failure.value.requested_cursor == 0
    assert failure.value.minimum_cursor == 3
    page = store.read_events(after=3)
    assert [record.event_id for record in page.events] == [4, 5]
    reopened = Store(tmp_path / "state.db", event_retention=2)
    assert reopened.append_event("worker", "w", Event("scan", StageStatus.COMPLETED)).event_id == 6
    with pytest.raises(ReplayGap):
        reopened.read_events(after=3)
    assert reopened.read_events(after=4).next_cursor == 6


@pytest.mark.parametrize("options", [{"limit": 0}, {"limit": 1001}, {"limit": True}, {"after": -1}, {"after": True}, {"after": 1.5}, {"after": 100}])
def test_invalid_replay_bounds(tmp_path, options):
    from h3server.store import Store
    with pytest.raises(ValueError):
        Store(tmp_path / "state.db").read_events(**options)


@pytest.mark.parametrize("retention", [0, -1, True, 1.5])
def test_invalid_retention(tmp_path, retention):
    from h3server.store import Store
    with pytest.raises(ValueError):
        Store(tmp_path / "state.db", event_retention=retention)


@pytest.mark.parametrize("event", [Event("", StageStatus.RUNNING), Event("x", "unknown"), Event("x", StageStatus.RUNNING, message=1)])
def test_invalid_event_is_not_persisted(tmp_path, event):
    from h3server.store import Store
    store = Store(tmp_path / "state.db")
    with pytest.raises(ValueError):
        store.append_event("worker", "w", event)
    assert store.read_events().events == ()


@pytest.mark.parametrize("payload", ['{}', 'not json', '{"stage":"x","status":"unknown","message":"","progress":null}',
    '{"stage":"x","status":"running","message":"","progress":{"completed":2,"unit":"steps","total":1}}',
    '{"stage":"x","status":"running","message":"","progress":{"completed":NaN,"unit":"steps","total":null}}',
    '{"stage":"x","stage":"y","status":"running","message":"","progress":null}'])
def test_corrupted_event_is_not_replayed(tmp_path, payload):
    from h3server.store import CorruptStore, Store
    store = Store(tmp_path / "state.db")
    store.append_event("worker", "w", Event("x", StageStatus.RUNNING))
    with closing(sqlite3.connect(tmp_path / "state.db", isolation_level=None)) as db:
        db.execute("UPDATE events SET payload=?", (payload,))
    with pytest.raises(CorruptStore):
        store.read_events()
    with pytest.raises(CorruptStore):
        Store(tmp_path / "state.db")


@pytest.mark.parametrize("sql", [
    "DELETE FROM events WHERE event_id=2",
    "UPDATE event_state SET minimum_cursor=-1",
    "DELETE FROM event_state",
])
def test_live_replay_metadata_corruption_fails_closed(tmp_path, sql):
    from h3server.store import CorruptStore, Store
    store = Store(tmp_path / "state.db")
    for _ in range(3):
        store.append_event("worker", "w", Event("scan", StageStatus.RUNNING))
    with closing(sqlite3.connect(tmp_path / "state.db", isolation_level=None)) as db:
        db.execute(sql)
    with pytest.raises(CorruptStore):
        store.read_events()
    with pytest.raises(CorruptStore):
        Store(tmp_path / "state.db")
