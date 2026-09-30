from types import SimpleNamespace
from pathlib import Path
import pytest
from h3server.backends import pruned
from h3server.backends.catalog import get_artifact_set


def test_preflight_does_not_resolve_undownloaded_weights(tmp_path):
    selected=get_artifact_set('h3-convrot-mixed')
    def unavailable(*args):
        pytest.fail('environment-only preflight must not resolve missing weight files')
    folders=SimpleNamespace(get_full_path=unavailable)
    assert pruned.runtime_model_files(tmp_path,selected,folders,prepare_weights=False)==[tmp_path/a.local_relative_path for a in selected.artifacts]


def test_generation_still_requires_resolved_weight_paths(tmp_path):
    selected=get_artifact_set('h3-convrot-mixed')
    with pytest.raises(ValueError,match='unavailable'):
        pruned.runtime_model_files(tmp_path,selected,SimpleNamespace(get_full_path=lambda *a:None),prepare_weights=True)
    paths=pruned.runtime_model_files(tmp_path,selected,SimpleNamespace(get_full_path=lambda *a:'/verified/weight'),prepare_weights=True)
    assert Path('/verified/weight') in paths
