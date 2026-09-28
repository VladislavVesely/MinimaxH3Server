"""Durable event envelopes and strict JSON codecs; no IO at import time."""

from dataclasses import asdict, dataclass
import json
import math

from h3server.domain import Event, Progress, StageStatus


MAX_EVENT_PAGE = 1000


class ReplayGap(ValueError):
    """The caller must refresh its state snapshot before resuming at minimum_cursor."""

    def __init__(self, requested_cursor: int, minimum_cursor: int):
        self.requested_cursor = requested_cursor
        self.minimum_cursor = minimum_cursor
        super().__init__(f"events after {requested_cursor} expired; minimum cursor is {minimum_cursor}")


@dataclass(frozen=True)
class EventRecord:
    event_id: int
    subject_type: str
    subject_id: str
    event: Event


@dataclass(frozen=True)
class EventPage:
    events: tuple[EventRecord, ...]
    next_cursor: int
    has_more: bool


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected nonempty text")
    return value


def _json_value(value: object) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item)
        return
    raise ValueError("expected finite JSON data with string object keys")


def _dump(value: object) -> str:
    _json_value(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _load(value: str) -> object:
    result = json.loads(value, object_pairs_hook=_pairs)
    _json_value(result)
    return result


def _keys(value: object, expected: set[str]) -> dict:
    if type(value) is not dict or set(value) != expected:
        raise ValueError("invalid record fields")
    return value


def _event_from_data(value: object) -> Event:
    data = _keys(value, {"stage", "status", "message", "progress"})
    _text(data["stage"])
    if not isinstance(data["message"], str):
        raise ValueError("event message must be text")
    progress = data["progress"]
    if progress is not None:
        progress = _keys(progress, {"completed", "unit", "total"})
        _text(progress["unit"])
        progress = Progress(**progress)
    return Event(data["stage"], StageStatus(data["status"]), data["message"], progress)


def encode_event(event: Event) -> str:
    if not isinstance(event, Event):
        raise ValueError("expected a domain Event")
    data = asdict(event)
    data["status"] = StageStatus(event.status).value
    _event_from_data(data)
    return _dump(data)


def decode_event(payload: str) -> Event:
    return _event_from_data(_load(payload))
