"""Server-owned global character records; media stays in immutable AssetStore.

All mutations share the asset deletion lock. Corrupt metadata fails closed so
job cleanup never treats unreadable library references as unreferenced assets.
"""
from contextlib import contextmanager
import json
import os
import uuid
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, model_validator
from h3server.assets import AssetStore
from h3server.backends.base import regular_file, read_json, atomic_json

ID = Annotated[str, Field(pattern=r'^[a-f0-9]{32}$')]
AssetID = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class VariantInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: ID
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=500)
    image_asset_id: AssetID

    @model_validator(mode='after')
    def named(self):
        if not self.name.strip(): raise ValueError('Empty name')
        return self


class CharacterInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    project_id: None = None
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=1000)
    voice_asset_id: AssetID | None = None
    voice_description: str = Field(default='', max_length=500)
    variants: list[VariantInput] = Field(min_length=1, max_length=16)

    @model_validator(mode='after')
    def bounded(self):
        if not self.name.strip(): raise ValueError('Empty name')
        if len({v.id for v in self.variants}) != len(self.variants): raise ValueError('Duplicate variant')
        data = self.model_dump(include=set(CharacterInput.model_fields))
        if len(json.dumps(data, ensure_ascii=False).encode()) > 14000: raise ValueError('Character metadata limit')
        return self


class CharacterUpdate(CharacterInput):
    revision: int = Field(ge=1)


class RevisionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    revision: int = Field(ge=1)


class Record(CharacterInput):
    id: ID
    revision: int = Field(ge=1)


class RevisionConflict(ValueError): pass


class CharacterStore:
    def __init__(self, paths): self.paths = paths

    @contextmanager
    def lock(self):
        import fcntl
        with regular_file(self.paths, 'state/assets.lock', flags=os.O_CREAT|os.O_RDWR, mode='r+b', create_parents=True) as guard:
            fcntl.flock(guard, fcntl.LOCK_EX)
            yield

    def _read(self):
        try: value = read_json(self.paths, 'state/characters.json', limit=4*1024*1024)
        except FileNotFoundError: return []
        if type(value) is not dict or set(value) != {'schema_version', 'characters'} or type(value['schema_version']) is not int or value['schema_version'] != 1:
            raise ValueError('Invalid character library')
        rows = value['characters']
        if type(rows) is not list or len(rows) > 256: raise ValueError('Character library limit')
        records = [Record.model_validate(row).model_dump() for row in rows]
        if len({r['id'] for r in records}) != len(records): raise ValueError('Duplicate character')
        return records

    def _write(self, rows):
        value = dict(schema_version=1, characters=rows)
        if len(rows) > 256 or len(json.dumps(value).encode()) > 4*1024*1024: raise ValueError('Character library limit')
        atomic_json(self.paths, 'state/characters.json', value)

    def list(self):
        with self.lock(): return self._read()

    def get(self, id):
        return self._find(self.list(), id)

    @staticmethod
    def _find(rows, id):
        for row in rows:
            if row['id'] == id: return row
        raise KeyError(id)

    def _validated(self, data):
        value = CharacterInput.model_validate(data).model_dump()
        assets = AssetStore(self.paths)
        for variant in value['variants']:
            if assets.metadata(variant['image_asset_id'])['kind'] != 'image': raise ValueError('Expected image')
        if value['voice_asset_id']:
            info = assets.metadata(value['voice_asset_id'])
            if info['kind'] != 'audio' or info['mime'] != 'audio/mpeg': raise ValueError('Expected MP3 voice')
        return value

    def create(self, data):
        with self.lock():
            rows = self._read()
            row = dict(self._validated(data), id=uuid.uuid4().hex, revision=1)
            self._write(rows+[row])
            return row

    def update(self, id, data, revision):
        with self.lock():
            rows = self._read(); old = self._find(rows, id)
            if old['revision'] != revision: raise RevisionConflict('Character changed')
            row = dict(self._validated(data), id=id, revision=revision+1)
            self._write([row if r['id'] == id else r for r in rows])
            return row

    def delete(self, id, revision):
        with self.lock():
            rows = self._read(); old = self._find(rows, id)
            if old['revision'] != revision: raise RevisionConflict('Character changed')
            self._write([r for r in rows if r['id'] != id])
            return {'deleted': id}

    def protected_assets_locked(self):
        """Caller already holds state/assets.lock (do not recursively flock)."""
        result = set()
        for row in self._read():
            result.update(v['image_asset_id'] for v in row['variants'])
            if row['voice_asset_id']: result.add(row['voice_asset_id'])
        return result
