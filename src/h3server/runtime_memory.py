"""Server-owned, strictly bounded startup policy; no arbitrary environment hooks."""
import hashlib
import json
import math
import os

DEFAULTS = {'reserve_vram_gib': None, 'offload_mode': 'automatic', 'expandable_segments': False}
PATH = 'config/runtime-memory.json'
ENV = 'H3_RUNTIME_MEMORY_SETTINGS'


def validate(value):
    if type(value) is not dict or set(value) != set(DEFAULTS):
        raise ValueError('invalid memory settings fields')
    reserve = value['reserve_vram_gib']
    if reserve is not None and (type(reserve) not in (int, float) or not math.isfinite(reserve) or not 0 <= reserve <= 64):
        raise ValueError('invalid VRAM reserve')
    if value['offload_mode'] not in ('automatic', 'low_vram') or type(value['expandable_segments']) is not bool:
        raise ValueError('invalid memory settings')
    return {**value, 'reserve_vram_gib': float(reserve) if reserve is not None else None}


def encoded(value):
    return json.dumps(validate(value), sort_keys=True, separators=(',', ':'), allow_nan=False)


def revision(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def load(paths):
    from h3server.backends.base import read_json
    try:
        record = read_json(paths, PATH, limit=4096)
    except FileNotFoundError:
        return dict(DEFAULTS)
    if type(record) is not dict or set(record) != {'schema', 'settings'} or type(record['schema']) is not int or record['schema'] != 1:
        raise ValueError('invalid memory settings schema')
    return validate(record['settings'])


def environment(settings):
    result = {ENV: encoded(settings)}
    if settings['expandable_segments']:
        result['PYTORCH_CUDA_ALLOC_CONF'] = 'expandable_segments:True'
    return result


def startup_settings():
    raw = os.environ.get(ENV)
    if raw is None:
        return dict(DEFAULTS)
    if len(raw) > 4096:
        raise ValueError('memory startup policy too large')
    return validate(json.loads(raw))


def configure_comfy(args):
    policy = startup_settings()
    args.reserve_vram = policy['reserve_vram_gib']
    args.lowvram = policy['offload_mode'] == 'low_vram'
    if args.lowvram:
        args.highvram = args.novram = args.gpu_only = False
    return policy


# Shared HTTP shape. GPU startup helpers above never evaluate user code/flags.
from pydantic import BaseModel, ConfigDict, Field, field_validator


class MemorySettingsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    settings: dict
    revision: str = Field(pattern=r'^[a-f0-9]{64}$')

    @field_validator('settings')
    @classmethod
    def validated_settings(cls, value):
        return validate(value)
