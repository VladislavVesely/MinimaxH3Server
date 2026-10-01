"""Runtime identity is independent of the daemon's currently running venv."""
import pytest

from h3server.backends.base import atomic_json
from h3server.backends.comfy_runtime import runtime_record
from h3server.paths import AppPaths

INT8 = 'h3-convrot-int8'
MIXED = 'h3-convrot-mixed'
Q4 = 'h3-gguf-q4-k-m'
Q5 = 'h3-gguf-q5-k-m'
ARTIFACT = {'release_id': 'test-release', 'filename': 'h3server.whl', 'sha256': 'c' * 64}


def environment(paths, profile, identifier):
    root = paths.root / 'environments' / identifier
    root.mkdir(parents=True, mode=0o700)
    resolved = 'torch==fixture\n'
    (root / 'resolved.txt').write_text(resolved)
    atomic_json(paths, f'environments/{identifier}/runtime.json', runtime_record(profile, resolved))
    atomic_json(paths, f'environments/{identifier}/readiness.json', {'environment_ready': True})
    atomic_json(paths, f'config/installation-{identifier}.json', {
        'profile': profile, 'environment': str(root), 'artifact': ARTIFACT,
    })
    return root


@pytest.fixture
def runtimes(tmp_path, monkeypatch):
    from h3server.remote import runtimes as module
    paths = AppPaths(tmp_path)
    seen = []
    def probe(paths, profile, identifier, lock_fd):
        import os
        assert os.fstat(lock_fd).st_uid == os.getuid()
        seen.append((profile, identifier))
        return {'environment_ready': True, 'environment': str(paths.environments / identifier)}
    monkeypatch.setattr(module, 'probe_environment', probe)
    return module.RuntimeBindings(paths), paths, seen


def test_missing_environment_is_not_a_runtime_family_incompatibility(runtimes):
    bindings, paths, _ = runtimes
    assert bindings.resolve(Q5) is None
    assert bindings.family(Q4) == bindings.family(Q5)
    assert bindings.family(INT8) == bindings.family(MIXED)
    assert bindings.family(INT8) != bindings.family(Q5)
    assert not (paths.root / bindings.registry).exists(), 'Read-only inventory must not publish state'


def test_two_runtime_families_coexist_and_use_their_own_interpreters(runtimes):
    bindings, paths, seen = runtimes
    first = environment(paths, INT8, 'a' * 64)
    second = environment(paths, Q4, 'b' * 64)
    bindings.publish(INT8, 'a' * 64, artifact=ARTIFACT)
    bindings.publish(Q5, 'b' * 64, artifact=ARTIFACT)
    assert bindings.resolve(INT8)['python'] == str(first / 'bin/python')
    assert bindings.resolve(Q5)['python'] == str(second / 'bin/python')
    assert seen == [(INT8, 'a' * 64), (Q5, 'b' * 64)]
    from h3server.remote.runtimes import RuntimeBindings
    assert RuntimeBindings(paths).resolve(INT8) == bindings.resolve(INT8)


def test_incompatible_environment_cannot_be_bound_by_renaming_the_profile(runtimes):
    bindings, paths, seen = runtimes
    environment(paths, INT8, 'a' * 64)
    with pytest.raises(ValueError, match='family'):
        bindings.publish(Q5, 'a' * 64, artifact=ARTIFACT)
    assert seen == []
    assert bindings.resolve(Q5) is None


def test_release_and_freeze_are_part_of_binding(runtimes):
    bindings, paths, seen = runtimes
    root = environment(paths, Q5, 'b' * 64)
    with pytest.raises(ValueError, match='release'):
        bindings.publish(Q5, 'b' * 64, artifact={**ARTIFACT, 'sha256': 'd' * 64})
    bindings.publish(Q5, 'b' * 64, artifact=ARTIFACT)
    (root / 'resolved.txt').write_text('torch==different\n')
    with pytest.raises(ValueError):
        bindings.resolve(Q5)


@pytest.mark.parametrize('identifier', ['../escape', '/tmp/python', 'a' * 63, 'A' * 64])
def test_environment_identifier_is_not_a_caller_supplied_executable(runtimes, identifier):
    bindings, _, _ = runtimes
    with pytest.raises(ValueError):
        bindings.publish(Q5, identifier, artifact=ARTIFACT)


def test_failed_native_verification_never_publishes_binding(runtimes, monkeypatch):
    bindings, paths, _ = runtimes
    environment(paths, Q5, 'b' * 64)
    monkeypatch.setattr('h3server.remote.runtimes.probe_environment', lambda *args: {'environment_ready': False})
    with pytest.raises(ValueError, match='verification'):
        bindings.publish(Q5, 'b' * 64, artifact=ARTIFACT)
    assert bindings.resolve(Q5) is None


def test_probe_cannot_certify_the_daemon_environment_instead(runtimes, monkeypatch):
    bindings, paths, _ = runtimes
    environment(paths, Q5, 'b' * 64)
    monkeypatch.setattr('h3server.remote.runtimes.probe_environment', lambda *args: {
        'environment_ready': True, 'environment': str(paths.environments / ('a' * 64)),
    })
    with pytest.raises(ValueError, match='verification'):
        bindings.publish(Q5, 'b' * 64, artifact=ARTIFACT)
    assert bindings.resolve(Q5) is None


def test_symlinked_receipt_is_rejected(runtimes):
    bindings, paths, _ = runtimes
    root = environment(paths, Q5, 'b' * 64)
    receipt = root / 'readiness.json'
    copy = root / 'other.json'
    receipt.rename(copy)
    receipt.symlink_to(copy)
    with pytest.raises((ValueError, OSError)):
        bindings.publish(Q5, 'b' * 64, artifact=ARTIFACT)


def test_invalid_registry_is_not_treated_as_empty_inventory(runtimes):
    bindings, paths, _ = runtimes
    atomic_json(paths, bindings.registry, {'schema': 99, 'profiles': {}})
    with pytest.raises(ValueError, match='registry'):
        bindings.resolve(Q5)


def test_hardlinked_receipt_is_rejected(runtimes):
    import os
    bindings, paths, _ = runtimes
    root = environment(paths, Q5, 'b' * 64)
    os.link(root / 'resolved.txt', root / 'alias.txt')
    with pytest.raises((ValueError, OSError)):
        bindings.publish(Q5, 'b' * 64, artifact=ARTIFACT)


def test_group_writable_environment_is_rejected(runtimes):
    bindings, paths, _ = runtimes
    root = environment(paths, Q5, 'b' * 64)
    root.chmod(0o770)
    with pytest.raises(ValueError, match='permissions'):
        bindings.publish(Q5, 'b' * 64, artifact=ARTIFACT)


def test_environment_changes_during_probe_are_not_published(runtimes, monkeypatch):
    bindings, paths, _ = runtimes
    root = environment(paths, Q5, 'b' * 64)
    def probe(*args):
        (root / 'readiness.json').write_text('{"environment_ready": true, "changed": true}')
        return {'environment_ready': True, 'environment': str(root)}
    monkeypatch.setattr('h3server.remote.runtimes.probe_environment', probe)
    with pytest.raises(ValueError, match='changed'):
        bindings.publish(Q5, 'b' * 64, artifact=ARTIFACT)
    assert bindings.resolve(Q5) is None
