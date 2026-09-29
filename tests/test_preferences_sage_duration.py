"""Focused coverage for remembered settings and the optional attention request."""
import json
import os

import pytest
from pydantic import ValidationError

from h3server.controller.preferences import Preferences
from h3server.domain import GenerationSettings
from h3server.remote.app import GenerationInput


def test_private_preferences_roundtrip_and_forget(tmp_path):
    prefs = Preferences(tmp_path / 'controller')
    prefs.remember(('example.invalid', 22, 'tester'), 'dummy-password')
    prefs.settings(True, True)
    reopened = Preferences(prefs.directory)
    assert reopened.saved()['password'] == 'dummy-password'
    assert 'dummy-password' not in json.dumps(reopened.public())
    assert reopened.public()['use_sage'] is True
    if os.name == 'posix':
        assert prefs.path.stat().st_mode & 0o777 == 0o600
        assert prefs.directory.stat().st_mode & 0o777 == 0o700
    reopened.forget()
    assert reopened.saved() is None
    assert reopened.public()['install_sage'] is True


@pytest.mark.skipif(os.name != 'posix', reason='POSIX permission checks')
def test_broad_permissions_and_symlinks_are_rejected(tmp_path):
    prefs = Preferences(tmp_path / 'controller')
    prefs.settings(False, False)
    prefs.path.chmod(0o644)
    with pytest.raises(ValueError):
        prefs.public()
    prefs.path.unlink()
    other = tmp_path / 'other.json'
    other.write_text('{}')
    prefs.path.symlink_to(other)
    with pytest.raises(ValueError):
        prefs.public()
    assert other.read_text() == '{}'


@pytest.mark.parametrize(('seconds', 'frames'), [(5, 124), (5.5, 124), (10, 243), (15, 345)])
def test_duration_uses_nearest_supported_frames(seconds, frames):
    request = GenerationInput(prompt='check', duration=seconds).to_request()
    assert request.settings.num_frames == frames
    assert request.settings.duration == frames / 24


def test_sage_is_explicit_strict_and_independent_of_sparse_attention():
    assert GenerationSettings().sage_attention is False
    request = GenerationInput(prompt='check', profile='h3-convrot-int8', sage_attention=True).to_request()
    assert request.settings.sage_attention is True
    assert request.sparse_attention is None
    with pytest.raises(ValidationError):
        GenerationInput(prompt='check', sage_attention='true')
    with pytest.raises(ValueError):
        GenerationSettings(sage_attention=1)


def test_optional_dependency_exception_is_exact_and_receipted(monkeypatch):
    from h3server.backends.comfy_runtime import dependencies_match
    from h3server.backends import sage_attention
    base = 'h3server==0.1.0\ntorch==2.14.0\n'
    addition = 'sageattention==1.0.6\n'
    monkeypatch.setattr(sage_attention, 'installed', lambda paths: False)
    assert not dependencies_match(None, base, base + addition)
    assert dependencies_match(None, base, base + addition, allow_unverified_sage=True)
    monkeypatch.setattr(sage_attention, 'installed', lambda paths: True)
    assert dependencies_match(None, base, base + addition)
    assert not dependencies_match(None, base, base + addition + 'unknown==1\n')
    assert not dependencies_match(None, base, base.replace('2.14.0', '2.15.0') + addition)
    assert not dependencies_match(None, base, base + 'sageattention==2.0.0\n')
    assert not dependencies_match(None, base, base + addition * 2)


@pytest.mark.skipif(os.name != 'posix', reason='Linux target installer lock')
def test_failed_gpu_probe_removes_only_the_addon(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from h3server.paths import AppPaths
    from h3server.remote import features
    from h3server.backends import comfy_runtime
    paths = AppPaths(tmp_path / 'root')
    paths.root.mkdir(mode=0o700)
    prefix = paths.environments / ('a' * 64)
    prefix.mkdir(parents=True)
    (prefix / 'resolved.txt').write_text('torch==2.14.0\n')
    monkeypatch.setattr(comfy_runtime, 'verify_provisioned', lambda *args, **kwargs: prefix)
    monkeypatch.setattr(features.metadata, 'version', lambda name: features.VERSION)
    calls, ended = [], []
    class IO:
        def __init__(self, **kwargs):
            pass
        def run(self, argv, **kwargs):
            calls.append(argv)
            if argv[-1] == 'h3server.backends.sage_attention':
                raise RuntimeError('GPU probe failed')
    monkeypatch.setattr(features, 'ProcessIO', IO)
    worker = SimpleNamespace(paths=paths, backend=SimpleNamespace(profiles=('h3-convrot-int8',)),
                             end_maintenance=ended.append)
    installer = features.SageInstaller(worker)
    installer._run('a' * 32)
    assert calls[-1][2:] == ('pip', 'uninstall', '--yes', 'sageattention')
    assert ended == ['a' * 32]
    assert installer._read()['state'] == 'failed'
    assert 'GPU probe failed' in installer._read()['error']
    assert (prefix / 'resolved.txt').read_text() == 'torch==2.14.0\n'
