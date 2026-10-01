"""Uniform pinned model identities for pruned and legacy runtime families."""
import hashlib
import json
from types import SimpleNamespace
from h3server.backends import load_profiles, get_profile
from h3server.backends.catalog import PRUNED_PROFILE_NAMES, get_artifact_set
from h3server.downloads import _selected_manifest, _binding, _snapshot_id

MODEL_NAMES=tuple(p.name for p in load_profiles(include_pruned=True))


def selection(name):
    get_profile(name)
    if name in PRUNED_PROFILE_NAMES:return get_artifact_set(name)
    manifest=_selected_manifest(name)
    return SimpleNamespace(digest=hashlib.sha256(json.dumps(_binding(manifest),sort_keys=True).encode()).hexdigest(),
        weight_bytes=manifest['model']['total_bytes'],runtime_id='diffusers-minimax-h3-t2va',
        artifacts=tuple(SimpleNamespace(local_relative_path=e['rfilename'],size_bytes=e['size'],sha256=e.get('lfs',{}).get('sha256',e.get('blobId'))) for e in manifest['model']['files']))


def snapshot_id(name):return _snapshot_id(_selected_manifest(name))
