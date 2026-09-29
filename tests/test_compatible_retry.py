"""Audited predecessor bytes, original plan/receipt, current trusted worker only."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path

import pytest

from h3server.controller.deploy import Deployment, ReleaseBundle
from h3server.controller.target import snapshot
from h3server.installer.compatibility import PREVIOUS_ARTIFACT, PREVIOUS_SHA256, compatible_plan, previous_payload
from h3server.installer.recovery import Recovery, service_plan
from h3server.installer.runner import Installer
from h3server.installer.steps import CommandFailure
from h3server.paths import AppPaths
from h3server.store import Store, canonical_plan_digest
from test_install_plan import approval, facts
from test_recovery import SystemIO
from test_setup_coordinator import flow, PROFILE
from test_controller_security import API


@pytest.fixture
def old_failure(flow, tmp_path, monkeypatch):
    app, client, headers, calls, passwords, state, sftp = flow
    home = tmp_path / 'target-home'
    home.mkdir()
    monkeypatch.setattr(app.state.connection, 'remote_home', lambda: str(home))
    monkeypatch.setattr(sftp, 'path', Path)
    wheel = tmp_path / PREVIOUS_ARTIFACT.filename
    wheel.write_bytes(previous_payload())
    prior = Deployment(app.state.connection, ReleaseBundle(wheel, PREVIOUS_SHA256))
    def make(profile=PROFILE):
        prior.make_plan(profile)
        paths = AppPaths(prior.root)
        for directory in paths.directories():
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        store = Store(paths.state / 'setup.sqlite3')
        system = SystemIO()
        original = system.run
        def run(argv, **kw):
            if argv == ('cat', '/etc/os-release'):
                return 'ID=' + prior.plan['platform']['distribution'] + '\nVERSION_ID=' + prior.plan['platform']['version']
            if argv[:2] == ('loginctl', 'show-user'):
                return 'yes\n'
            return original(argv, **kw)
        system.run = run
        recovery = Recovery(paths, store, io=system, uid=os.getuid())
        setup = Installer(paths, store, io=system).create(prior.plan)
        recovery.prepare(setup.setup_id, approval(prior.plan, privileged=True),
                         service_digest=canonical_plan_digest(service_plan(paths, prior.plan, uid=os.getuid())))
        system.fail = 'pip install'
        with pytest.raises(CommandFailure):
            recovery.resume(setup.setup_id, setup.plan_digest)
        actions = []
        def target(**kw):
            actions.append(kw)
            assert kw['artifact'] == app.state.deployment.release.artifact
            assert kw['action'] == 'snapshot'
            return snapshot(paths, store, 0, kw['digest'])
        monkeypatch.setattr(app.state.connection, 'deployment_action', target)
        return paths, store, setup, actions
    return app, client, headers, prior, make


@pytest.mark.parametrize('variant', [False, True])
def test_compatible_restore_retains_two_distinct_valid_plan_bindings(old_failure, monkeypatch, variant):
    app, client, headers, prior, make = old_failure
    if variant:
        monkeypatch.setattr(app.state.connection, 'inspect_install', lambda **kw: facts(distribution='debian', version='12'))
    paths, store, setup, actions = make()
    plan_before = json.dumps(prior.plan, sort_keys=True)
    units = {p.name: p.read_bytes() for p in (paths.config / 'systemd').iterdir()}
    receipt = (paths.bootstrap / setup.plan_digest / 'recovery.json').read_bytes()
    pointer = (paths.config / 'deployment.json').read_bytes()
    assert client.post(API + '/setup/restore', json={}, headers=headers).json()['error']['code'] == 'matching_release_required'
    result = client.post(API + '/setup/restore-compatible', json={}, headers=headers)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data['plan_digest'] == setup.plan_digest
    assert data['snapshot']['retry']['eligible']
    assert json.dumps(app.state.deployment.plan, sort_keys=True) == plan_before
    assert (paths.config / 'deployment.json').read_bytes() == pointer
    assert (paths.bootstrap / setup.plan_digest / 'recovery.json').read_bytes() == receipt
    assert {p.name: p.read_bytes() for p in (paths.config / 'systemd').iterdir()} == units
    assert all(a['artifact'] != PREVIOUS_ARTIFACT for a in actions)


@pytest.mark.parametrize('change', ['unknown-release', 'contract', 'unit', 'active', 'worker'])
def test_compatible_restore_fails_closed(old_failure, change):
    app, client, headers, prior, make = old_failure
    paths, store, setup, actions = make()
    if change == 'unknown-release':
        pointer = paths.config / 'deployment.json'
        data = json.loads(pointer.read_bytes())
        data['artifact']['sha256'] = 'a' * 64
        pointer.write_text(json.dumps(data)); pointer.chmod(0o600)
    elif change == 'contract':
        plan = paths.bootstrap / setup.plan_digest / 'plan.json'
        data = json.loads(plan.read_bytes()); data['requirements'].append('av==1.0.0')
        plan.write_text(json.dumps(data)); plan.chmod(0o600)
    elif change == 'unit':
        (paths.config / 'systemd/h3-bootstrap.service').write_text('changed')
    elif change == 'active':
        from h3server.backends.base import atomic_json
        atomic_json(paths, 'state/active-release.json', {'environment': setup.plan_digest})
    if change == 'worker':
        with Installer(paths, store).lock():
            result = client.post(API + '/setup/restore-compatible', json={}, headers=headers)
    else:
        result = client.post(API + '/setup/restore-compatible', json={}, headers=headers)
    assert result.status_code == 409, result.text
    assert not app.state.deployment.compatible
    assert all(a['action'] == 'snapshot' for a in actions)


def test_packaged_compatibility_payload_has_trusted_hash():
    payload = previous_payload()
    assert len(payload) == 187946
    assert hashlib.sha256(payload).hexdigest() == PREVIOUS_SHA256
    assert PREVIOUS_ARTIFACT.sha256 == PREVIOUS_SHA256


def test_audited_predecessor_service_contract_is_unchanged():
    # Compare syntax without importing/executing predecessor code. A future unit
    # contract change must remove/review this compatibility proof explicitly.
    import ast
    import inspect
    import io
    import zipfile
    from h3server.installer import recovery
    names = {'BOOTSTRAP_UNIT', 'DAEMON_UNIT', '_quote', '_scalar_path',
             'render_units', '_unit_path', 'service_plan'}
    def contract(source):
        result = {}
        for node in ast.parse(source).body:
            name = (node.name if isinstance(node, ast.FunctionDef) else
                    node.targets[0].id if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) else None)
            if name in names:
                result[name] = ast.dump(node, include_attributes=False)
        assert set(result) == names
        return result
    with zipfile.ZipFile(io.BytesIO(previous_payload())) as archive:
        predecessor = archive.read('h3server/installer/recovery.py').decode()
    assert contract(predecessor) == contract(inspect.getsource(recovery))
