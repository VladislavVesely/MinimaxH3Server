"""Stale release Install regression: real Deployment/coordinator, controlled SSH IO."""
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
import sqlite3
import sys

import pytest

from h3server.controller.deploy import Deployment, ReleaseBundle
from h3server.controller.ssh import ControllerError
from h3server.backends.catalog import get_artifact_set
from test_deploy import wheel
from test_setup_coordinator import flow, start, finished


@pytest.fixture
def stale(flow, tmp_path, monkeypatch):
    app, client, headers, calls, passwords, state, sftp = flow
    old_dir = tmp_path / 'old'; old_dir.mkdir()
    old_wheel = wheel(old_dir, 'h3server/old_only.py')
    old = ReleaseBundle(old_wheel, hashlib.sha256(old_wheel.read_bytes()).hexdigest())
    assert old.artifact != app.state.deployment.release.artifact
    prior = Deployment(app.state.connection, old)
    prior.make_plan('h3-convrot-int8', sudo_password_available=True)
    root = sftp.path(prior.root)
    from h3server.store import Store
    Store(root / 'state/setup.sqlite3')
    (root / 'state/setup.sqlite3').chmod(0o600)
    calls.clear()
    # Fail the IO boundary if any post-handoff request tries to run old code.
    for name in ('recovery', 'deployment_action'):
        original = getattr(app.state.connection, name)
        def current_only(*args, _original=original, **kwargs):
            assert kwargs['artifact'] == app.state.deployment.release.artifact
            return _original(*args, **kwargs)
        monkeypatch.setattr(app.state.connection, name, current_only)
    return flow, prior, root


def test_explicit_install_rediscovers_unlaunched_different_release(stale):
    (app, client, headers, calls, _, _, _), prior, root = stale
    pointer = (root / 'config/deployment.json').read_bytes()
    old_plan = (root / 'bootstrap' / prior.document()['plan_digest'] / 'plan.json').read_bytes()
    with pytest.raises(ControllerError, match='matching_release_required'):
        app.state.deployment.restore(observation_only=True)
    assert (root / 'config/deployment.json').read_bytes() == pointer
    selected = get_artifact_set('h3-convrot-int8')
    assert start(client, headers, profile=selected.profile_name, artifact_set_digest=selected.digest).status_code == 202
    result = finished(app)
    assert result['state'] == 'completed', result
    assert calls == ['service-plan', 'prepare', 'activate', 'download']
    assert calls.count('prepare') == 1
    document = app.state.deployment.document()
    assert document['plan']['artifact'] == asdict(app.state.deployment.release.artifact)
    assert document['plan']['profile'] == selected.profile_name
    assert document['selection']['artifact_set_digest'] == selected.digest
    assert (root / 'bootstrap' / prior.document()['plan_digest'] / 'plan.json').read_bytes() == old_plan
    assert (root / 'releases' / prior.release.artifact.release_id / prior.release.artifact.filename).read_bytes() == prior.release.data


@pytest.mark.parametrize('evidence', ['approval.json', 'composite-install.json', 'status', 'boot.json',
    'active-release.json', 'operation.json', 'deployment-operation.json', 'maintenance.json', 'activation.json', 'checkpoint', 'malformed-db', 'wal', 'symlink'])
def test_unsafe_stale_records_never_stage_or_execute_old_release(stale, evidence):
    (app, client, headers, calls, _, _, _), prior, root = stale
    stage = root / 'bootstrap' / prior.document()['plan_digest']
    if evidence == 'checkpoint':
        with closing(sqlite3.connect(root / 'state/setup.sqlite3')) as db:
            db.execute('INSERT INTO setups VALUES (?, ?)', ('old', '{}'))
            db.commit()
    elif evidence == 'malformed-db':
        (root / 'state/setup.sqlite3').write_bytes(b'broken')
    elif evidence == 'wal':
        (root / 'state/setup.sqlite3-wal').write_bytes(b'')
    elif evidence == 'symlink':
        plan = stage / 'plan.json'; plan.rename(stage / 'saved.json'); plan.symlink_to(stage / 'saved.json')
    else:
        destination = stage if evidence in {'approval.json', 'composite-install.json', 'status', 'boot.json'} else root / 'state'
        (destination / evidence).write_bytes(b'')
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert start(client, headers).status_code == 202
    result = finished(app)
    assert result['state'] == 'failed' and result['error'] == 'stale_discovery_unproven', result
    assert not calls
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('lock', ['staging', 'installer', 'deployment'])
def test_stale_discovery_respects_held_not_merely_existing_lock(stale, lock):
    import fcntl
    import os
    (app, client, headers, calls, _, _, _), _, root = stale
    pointer = (root / 'config/deployment.json').read_bytes()
    fd = os.open(root / 'state' / (lock + '.lock'), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert start(client, headers).status_code == 202
        assert finished(app)['error'] == 'staging_lock_unavailable'
        assert not calls
        assert (root / 'config/deployment.json').read_bytes() == pointer
    finally:
        os.close(fd)
    # Existence after release is not work evidence.
    assert app.state.deployment.rediscover_stale_plan('h3-convrot-int8') is not None


@pytest.mark.parametrize('change', ['pointer', 'approval', 'database', 'holder-lost'])
def test_concurrent_change_or_lost_guard_does_not_publish(stale, monkeypatch, change):
    from contextlib import contextmanager
    (app, client, headers, calls, _, _, _), prior, root = stale
    pointer = root / 'config/deployment.json'
    expected = pointer.read_bytes()
    original = app.state.connection.inspect_install
    lost = False
    if change == 'holder-lost':
        transaction = app.state.connection._staging_transaction
        @contextmanager
        def guard(*args, **kwargs):
            with transaction(*args, **kwargs) as check:
                def checked():
                    check()
                    if lost:
                        raise ControllerError('staging_lock_lost')
                yield checked
        monkeypatch.setattr(app.state.connection, '_staging_transaction', guard)
    def inspect(**kw):
        nonlocal expected, lost
        if change == 'pointer':
            saved = json.loads(expected); saved['digest'] = 'a' * 64
            expected = json.dumps(saved).encode(); pointer.write_bytes(expected)
        elif change == 'approval':
            (root / 'bootstrap' / prior.document()['plan_digest'] / 'approval.json').write_bytes(b'partial')
        elif change == 'database':
            with closing(sqlite3.connect(root / 'state/setup.sqlite3')) as db:
                db.execute("INSERT INTO setups VALUES ('old', '{}')"); db.commit()
        else:
            lost = True
        return original(**kw)
    monkeypatch.setattr(app.state.connection, 'inspect_install', inspect)
    assert start(client, headers).status_code == 202
    assert finished(app)['error'] == ('staging_lock_lost' if change == 'holder-lost' else 'stale_discovery_unproven')
    assert pointer.read_bytes() == expected
    assert not calls
    assert len(list((root / 'bootstrap').iterdir())) == 1


@pytest.mark.parametrize('change', ['digest', 'artifact', 'root', 'malformed', 'config-link', 'release-link'])
def test_invalid_old_binding_is_never_replaced(stale, change):
    (app, client, headers, calls, _, _, _), prior, root = stale
    pointer = root / 'config/deployment.json'
    plan_path = root / 'bootstrap' / prior.document()['plan_digest'] / 'plan.json'
    if change == 'digest':
        plan_path.write_bytes(plan_path.read_bytes().replace(b'"blockers": []', b'"blockers": ["changed"]'))
    elif change == 'artifact':
        saved = json.loads(pointer.read_bytes()); saved['artifact']['sha256'] = 'a' * 64
        pointer.write_text(json.dumps(saved))
    elif change == 'root':
        from h3server.store import canonical_plan_digest
        plan = json.loads(plan_path.read_bytes()); plan['deployment_config']['root'] = '/different'
        digest = canonical_plan_digest(plan)
        plan_path.parent.rename(root / 'bootstrap' / digest)
        plan_path = root / 'bootstrap' / digest / 'plan.json'; plan_path.write_text(json.dumps(plan))
        saved = json.loads(pointer.read_bytes()); saved['digest'] = digest; pointer.write_text(json.dumps(saved))
    elif change == 'malformed':
        pointer.write_bytes(b'{')
    elif change == 'config-link':
        pointer.rename(root / 'config/saved.json'); pointer.symlink_to(root / 'config/saved.json')
    else:
        release = root / 'releases' / prior.release.artifact.release_id
        release.rename(root / 'releases/saved'); release.symlink_to(root / 'releases/saved', target_is_directory=True)
    expected = pointer.read_bytes()
    assert start(client, headers).status_code == 202
    assert finished(app)['error'] == 'stale_discovery_unproven'
    assert pointer.read_bytes() == expected and not calls


@pytest.mark.parametrize('database', [False, True])
def test_missing_remote_python_needs_no_old_code_or_remote_database_reader(stale, monkeypatch, database):
    from test_install_plan import facts
    from h3server.controller.deploy import _write
    (app, client, headers, calls, _, _, _), _, root = stale
    if not database:
        (root / 'state/setup.sqlite3').unlink()
    inspections = [facts(python_ready=False, packages=('python3-venv=1',),
        package_changes=('Inst python3-venv (1 Ubuntu [amd64])',)), facts()]
    monkeypatch.setattr(app.state.connection, 'inspect_install', lambda **kw: inspections.pop(0))
    def prerequisites(**kw):
        calls.append('prerequisites')
        assert kw['plan']['artifact'] == asdict(app.state.deployment.release.artifact)
        with app.state.connection.private_sftp() as sftp:
            _write(sftp, f"{kw['root']}/bootstrap/{kw['approval'].plan_digest}/status", b'waiting_for_replan\n')
    monkeypatch.setattr(app.state.connection, 'install_prerequisites', prerequisites)
    selected = get_artifact_set('h3-convrot-int8')
    assert start(client, headers, profile=selected.profile_name, artifact_set_digest=selected.digest).status_code == 202
    assert finished(app)['state'] == 'completed'
    assert calls == ['prerequisites', 'service-plan', 'prepare', 'activate', 'download']
    assert not inspections


@pytest.mark.skipif(sys.platform != 'linux', reason='target guardian requires Linux fd inode semantics and util-linux flock')
def test_source_owned_shell_guard_quotes_locks_and_releases_on_eof(tmp_path):
    """Run the actual Linux target guardian, including real shell/flock IO."""
    import fcntl
    import os
    import shutil
    import subprocess
    import sys
    from h3server.controller.discovery import staging_command
    root = tmp_path / "target ' $(touch forbidden)"; (root / 'state').mkdir(parents=True)
    env = dict(os.environ)
    assert shutil.which('flock') is not None
    for finish in (b'finish\n', b''):
        process = subprocess.Popen(staging_command(str(root), discovery=True, nonce='test'),
            shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            assert process.stdout.readline() == b'H3READY test\n'
            for name in ('staging', 'installer', 'deployment'):
                with (root / 'state' / (name + '.lock')).open('rb') as fd:
                    with pytest.raises(BlockingIOError):
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            output, error = process.communicate(finish, timeout=3)
            assert not output and not error
            assert process.returncode == (0 if finish else 1)
        finally:
            if process.poll() is None:
                process.kill(); process.communicate()
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        for name in ('staging', 'installer', 'deployment'):
            with (root / 'state' / (name + '.lock')).open('rb') as fd:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert not (tmp_path / 'forbidden').exists()
    lock = root / 'state/staging.lock'; lock.unlink(); lock.symlink_to(root / 'state/installer.lock')
    denied = subprocess.run(staging_command(str(root), discovery=True, nonce='test'),
        shell=True, input=b'finish\n', capture_output=True, env=env, timeout=3)
    assert denied.returncode != 0 and b'H3READY' not in denied.stdout


def test_production_ssh_guard_reentrant_cleanup_and_holder_loss(flow, monkeypatch):
    import re
    from test_ssh import FakeChannel
    from h3server.controller.ssh import SSHConnection
    app, _, _, _, _, _, sftp = flow
    ssh = app.state.connection
    root = '/home/test/MinimaxH3Server'
    sftp.path(root + '/state').mkdir(parents=True)
    channels = []
    class Guardian(FakeChannel):
        done = False
        def exec_command(self, command):
            self.output = ('H3READY ' + re.search(r'H3READY ([a-f0-9]{32})', command)[1] + '\n').encode()
        def exit_status_ready(self): return self.done
        def sendall(self, data):
            assert data == b'finish\n'
            self.done = True
    def channel(**kw):
        result = Guardian(ssh._transport.network); channels.append(result); return result
    monkeypatch.setattr(ssh._transport, 'open_session', channel)
    monkeypatch.setattr(ssh, '_staging_transaction', SSHConnection._staging_transaction.__get__(ssh))
    with ssh.staging_transaction(root, discovery=True) as check:
        with ssh.staging_transaction(root) as nested:
            assert check is nested
            check()
        assert len(channels) == 1
    assert channels[0].closed and channels[0].done and ssh._staging_owned is None
    with pytest.raises(ControllerError, match='staging_lock_lost'):
        with ssh.staging_transaction(root) as check:
            channels[-1].done = True
            check()
    assert channels[-1].closed and ssh._staging_owned is None
    with pytest.raises(ControllerError, match='staging_lock_scope_changed'):
        with ssh.staging_transaction(root):
            with ssh.staging_transaction(root, discovery=True):
                pytest.fail('must not upgrade')
    assert channels[-1].closed and ssh._staging_owned is None


@pytest.mark.parametrize('state', ['planned', 'running', 'ready'])
def test_real_old_release_checkpoint_remains_exact_release_bound(stale, state):
    from h3server.domain import StageStatus
    from h3server.store import Store, SetupState
    (app, client, headers, calls, _, _, _), prior, root = stale
    store = Store(root / 'state/setup.sqlite3')
    setup = store.create_setup(prior.plan)
    if state != 'planned':
        store.transition_setup(setup.setup_id, SetupState.AWAITING_APPROVAL)
        store.approve_setup(setup.setup_id, setup.plan_digest)
        store.transition_setup(setup.setup_id, SetupState.RUNNING)
        if state == 'ready':
            for stage in setup.stages:
                store.transition_stage(setup.setup_id, stage.name, StageStatus.RUNNING)
                store.transition_stage(setup.setup_id, stage.name, StageStatus.COMPLETED)
            store.transition_setup(setup.setup_id, SetupState.READY)
    expected = (root / 'config/deployment.json').read_bytes()
    assert start(client, headers).status_code == 202
    assert finished(app)['error'] == 'stale_discovery_unproven'
    assert (root / 'config/deployment.json').read_bytes() == expected
    assert not calls
    with pytest.raises(ControllerError, match='matching_release_required'):
        app.state.deployment.restore(expected_digest=setup.plan_digest, expected_root=prior.root, observation_only=True)


@pytest.mark.parametrize('method', ['install', 'install_once'])
def test_waiting_old_controller_cannot_approve_superseded_discovery(stale, method):
    from h3server.installer.planner import Approval
    (app, _, _, calls, _, _, _), prior, root = stale
    old = prior.document()
    app.state.deployment.rediscover_stale_plan('h3-convrot-int8')
    pointer = (root / 'config/deployment.json').read_bytes()
    calls.clear()
    with pytest.raises(ControllerError, match='selection_changed'):
        getattr(prior, method)(Approval(old['plan_digest'], True, True, True, True),
            service_digest=old['service_digest'], sudo_password='SSH-SECRET')
    assert not calls
    assert (root / 'config/deployment.json').read_bytes() == pointer
    assert sorted(p.name for p in (root / 'bootstrap' / old['plan_digest']).iterdir()) == ['plan.json']
