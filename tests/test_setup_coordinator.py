"""One-click routes use real Deployment/staging and controlled SSH IO, never a server."""
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from h3server.controller.app import create_app
from h3server.controller.ssh import ControllerError
from h3server.probe import CommandResult
from test_controller_security import API, ORIGIN, bootstrap, connect
from test_controller_reconnection import eventually
from test_deploy import LocalSFTP, bundle
from test_install_plan import facts
from test_ssh import Network

PROFILE = 'h3-bf16-cpu-offload-experimental'


@pytest.fixture
def flow(tmp_path, monkeypatch):
    network = Network()
    app = create_app(state_dir=tmp_path / 'trust', origin=ORIGIN, release=bundle(tmp_path),
                     socket_factory=network.socket, transport_factory=network.transport)
    ssh = app.state.connection
    sftp = LocalSFTP(tmp_path / 'remote'); sftp.base.mkdir()
    monkeypatch.setattr(ssh, 'open_sftp', lambda: sftp)
    monkeypatch.setattr(ssh, 'inspect_install', lambda **kw: facts())
    calls, passwords = [], []
    state = {'setup': None, 'operation': None, 'active_release': None, 'reboot': False}
    def check(password):
        passwords.append(password)
    monkeypatch.setattr(ssh, 'check_setup_password', check)
    def recovery(**kw):
        calls.append(kw['action'])
        if kw['action'] == 'service-plan':
            return CommandResult(json.dumps({'stages': ['services']}), returncode=0)
        if kw['action'] == 'prepare':
            passwords.append(kw['sudo_password'])
            state['setup'] = 'waiting_for_reboot' if state['reboot'] else 'ready'
        elif kw['action'] == 'reboot':
            passwords.append(kw['sudo_password'])
            state['setup'] = 'ready'
            ssh.close()
        elif kw['action'] == 'resume':
            state['setup'] = 'ready'
        return CommandResult(returncode=0)
    monkeypatch.setattr(ssh, 'recovery', recovery)
    def action(**kw):
        digest = kw['digest']
        if kw['action'] == 'snapshot':
            return {'setups': ([{'plan_digest': digest, 'state': state['setup']}] if state['setup'] else []),
                    'events': [], 'cursor': 0, 'has_more': False,
                    'active_release': state['active_release'], 'operation': state['operation']}
        calls.append(kw['action'])
        assert kw['data'].get('policy', 'reject') == 'reject'
        if kw['action'] == 'activate':
            assert kw['data']['activation_approved'] is True
            state['active_release'] = {'environment': digest}
        if kw['action'] == 'download':
            assert kw['data']['download_approved'] is True
            assert kw['data']['license_acknowledged'] is True
            assert kw['data']['artifact_set_digest'] == app.state.deployment.document()['selection']['artifact_set_digest']
        state['operation'] = {'operation': 'a' * 32, 'action': kw['action'], 'state': 'completed', 'plan_digest': digest}
        return {'state': 'accepted', 'operation': 'a' * 32}
    monkeypatch.setattr(ssh, 'deployment_action', action)
    with TestClient(app, base_url=ORIGIN) as client:
        headers = bootstrap(app, client)
        fp = connect(client, headers).json()['fingerprint']
        assert client.post(API + '/approve', json={'fingerprint': fp}, headers=headers).status_code == 200
        assert client.post(API + '/authenticate', json={'password': 'SSH-SECRET', 'reuse_for_setup': True}, headers=headers).status_code == 200
        app.state.setup.poll_interval = .01
        yield app, client, headers, calls, passwords, state, sftp


def start(client, headers, **kw):
    return client.post(API + '/setup/start', headers=headers,
                       json={'profile': PROFILE, 'request_id': 'test-one-click-1', **kw})


def finished(app):
    eventually(lambda: app.state.setup.status()['state'] in {'completed', 'failed', 'cancelled'})
    return app.state.setup.status()


def test_one_click_real_routes_stages_installs_activates_downloads(flow):
    app, client, headers, calls, passwords, _, sftp = flow
    result = start(client, headers)
    assert result.status_code == 202, result.text
    status = finished(app)
    assert status['state'] == 'completed', status
    assert calls == ['service-plan', 'prepare', 'activate', 'download']
    assert passwords == ['SSH-SECRET', 'SSH-SECRET']
    assert status['model_ready'] and not status['generation_tested']
    assert 'SSH-SECRET' not in json.dumps(status)
    assert all(b'SSH-SECRET' not in p.read_bytes() for p in sftp.base.rglob('*') if p.is_file())
    assert start(client, headers).json()['operation_id'] == status['operation_id']
    assert calls.count('prepare') == 1
    assert client.get(API + '/setup/status').json()['state'] == 'completed'


def test_one_click_requires_csrf_and_explicit_credential_reuse(flow):
    app, client, headers, calls, _, _, _ = flow
    assert start(client, {'origin': ORIGIN}).status_code == 403
    client.post(API + '/recovery/forget', json={}, headers=headers)
    response = start(client, headers)
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'setup_credential_required'
    assert not calls


def test_sudo_rejection_is_specific_safe_and_stops_before_install(flow, monkeypatch):
    app, client, headers, calls, _, _, _ = flow
    def reject(password):
        raise ControllerError('setup_sudo_password_rejected')
    monkeypatch.setattr(app.state.connection, 'check_setup_password', reject)
    assert start(client, headers).status_code == 202
    status = finished(app)
    assert status['error'] == 'setup_sudo_password_rejected'
    assert 'prepare' not in calls
    assert not app.state.recovery.status()['credential_retained']


def test_necessary_reboot_recovers_without_repeated_privileged_work(flow, monkeypatch):
    app, client, headers, calls, _, state, _ = flow
    state['reboot'] = True
    monkeypatch.setattr(app.state.connection, 'inspect_install', lambda **kw: facts(driver_working=False,
        driver_package='nvidia-driver-580', packages=('nvidia-driver-580=1',),
        package_changes=('Inst nvidia-driver-580 (1 Ubuntu [amd64])',)))
    assert start(client, headers).status_code == 202
    assert finished(app)['state'] == 'completed'
    assert calls.count('prepare') == calls.count('reboot') == 1
    assert calls[-2:] == ['activate', 'download']


def test_observation_does_not_hold_global_lock_and_cancel_clears_secret(flow, monkeypatch):
    app, client, headers, calls, _, state, _ = flow
    entered, release = threading.Event(), threading.Event()
    original = app.state.connection.deployment_action
    def blocked(**kw):
        if kw['action'] == 'snapshot':
            entered.set()
            assert release.wait(3)
        return original(**kw)
    monkeypatch.setattr(app.state.connection, 'deployment_action', blocked)
    try:
        assert start(client, headers).status_code == 202
        assert entered.wait(1)
        before = time.monotonic()
        assert client.get(API + '/setup/status').status_code == 200
        assert client.get(API + '/status').status_code == 200
        assert client.post(API + '/authenticate', json={'password': 'NEW'}, headers=headers).status_code == 409
        assert time.monotonic() - before < 1
        assert client.post(API + '/setup/cancel', json={}, headers=headers).status_code == 202
        assert not app.state.recovery.status()['credential_retained']
    finally:
        release.set()
    assert finished(app)['state'] == 'cancelled'
    assert 'prepare' not in calls


def test_prerequisite_round_replans_and_preserves_exact_selection(flow, monkeypatch):
    from h3server.backends.catalog import get_artifact_set
    from h3server.controller.deploy import _write
    app, client, headers, calls, passwords, _, _ = flow
    selected = get_artifact_set('h3-gguf-q4-k-m')
    inspections = [facts(python_ready=False, packages=('python3-venv=1',),
                         package_changes=('Inst python3-venv (1 Ubuntu [amd64])',)), facts()]
    monkeypatch.setattr(app.state.connection, 'inspect_install', lambda **kw: inspections.pop(0))
    def prerequisites(**kw):
        calls.append('prerequisites')
        passwords.append(kw['sudo_password'])
        assert kw['plan']['profile'] == selected.profile_name
        assert kw['plan']['artifact_set_digest'] == selected.digest
        from h3server.store import canonical_plan_digest
        assert kw['approval'].plan_digest == canonical_plan_digest(kw['plan'])
        with app.state.connection.private_sftp() as sftp:
            _write(sftp, f"{kw['root']}/bootstrap/{kw['approval'].plan_digest}/status", b'waiting_for_replan\n')
    monkeypatch.setattr(app.state.connection, 'install_prerequisites', prerequisites)
    assert start(client, headers, profile=selected.profile_name, artifact_set_digest='0' * 64).status_code == 409
    assert start(client, headers, profile=selected.profile_name, artifact_set_digest=selected.digest).status_code == 202
    status = finished(app)
    assert status['state'] == 'completed', status
    assert status['profile'] == selected.profile_name and status['artifact_set_digest'] == selected.digest
    assert calls == ['prerequisites', 'service-plan', 'prepare', 'activate', 'download']
    assert passwords == ['SSH-SECRET'] * 3
    assert not inspections


def test_ambiguous_install_loss_observes_checkpoint_without_relaunch(flow, monkeypatch):
    app, client, headers, calls, _, state, _ = flow
    original = app.state.connection.recovery
    def lost(**kw):
        result = original(**kw)
        if kw['action'] == 'prepare':
            app.state.connection.close()
            raise ControllerError('installer_launch_failed')
        return result
    monkeypatch.setattr(app.state.connection, 'recovery', lost)
    assert start(client, headers).status_code == 202
    assert finished(app)['state'] == 'completed'
    assert calls.count('prepare') == 1
    assert calls[-2:] == ['activate', 'download']


@pytest.mark.parametrize('failure', ['authentication_failed', 'host_key_changed', 'trust_required'])
def test_composite_recovery_security_failure_never_replays(flow, monkeypatch, failure):
    app, client, headers, calls, _, _, _ = flow
    original = app.state.connection.recovery
    def lost(**kw):
        result = original(**kw)
        if kw['action'] == 'prepare':
            app.state.connection.close()
        return result
    def denied(*args, **kw):
        raise ControllerError(failure)
    monkeypatch.setattr(app.state.connection, 'recovery', lost)
    monkeypatch.setattr(app.state.connection, 'connect', denied)
    assert start(client, headers).status_code == 202
    result = finished(app)
    assert result['state'] == 'failed' and result['error'] == failure, result
    assert calls.count('prepare') == 1
    assert 'activate' not in calls
    assert not app.state.recovery.status()['credential_retained']


@pytest.mark.parametrize('existing', ['ready', 'failed'])
def test_ready_and_resumable_checkpoints_do_not_repeat_privileged_install(flow, existing):
    app, client, headers, calls, passwords, state, _ = flow
    state['setup'] = existing
    assert start(client, headers).status_code == 202
    assert finished(app)['state'] == 'completed'
    assert 'prepare' not in calls and not passwords
    assert calls.count('resume') == (1 if existing == 'failed' else 0)


def test_running_foreign_operation_is_rejected_not_cancelled(flow):
    app, client, headers, calls, _, state, _ = flow
    assert client.post(API + '/setup/plan', json={'profile': PROFILE}, headers=headers).status_code == 200
    state['operation'] = {'operation': 'b' * 32, 'action': 'download', 'state': 'running',
                          'plan_digest': 'f' * 64}
    assert start(client, headers).status_code == 202
    result = finished(app)
    assert result['error'] == 'setup_remote_busy'
    assert calls == ['service-plan']


@pytest.mark.parametrize('action', ['expire', 'disconnect', 'connect', 'forget', 'shutdown'])
def test_lifecycle_stops_future_actions_and_drops_credential(flow, monkeypatch, action):
    app, client, headers, calls, _, _, _ = flow
    entered, release = threading.Event(), threading.Event()
    original = app.state.connection.inspect_install
    def blocked(**kw):
        entered.set()
        assert release.wait(3)
        return original(**kw)
    monkeypatch.setattr(app.state.connection, 'inspect_install', blocked)
    assert start(client, headers).status_code == 202
    assert entered.wait(1)
    closer = None
    try:
        if action == 'expire':
            app.state.session.invalidate()
            app.state.setup.expire()
        elif action == 'shutdown':
            closer = threading.Thread(target=app.state.setup.close)
            closer.start()
            eventually(lambda: not app.state.recovery.status()['credential_retained'])
        else:
            path, body = {'disconnect': ('/disconnect', {}),
                          'connect': ('/connect', {'host': 'other.example', 'username': 'other'}),
                          'forget': ('/recovery/forget', {})}[action]
            assert client.post(API + path, json=body, headers=headers).status_code == 409
        assert not app.state.recovery.status()['credential_retained']
        assert app.state.setup.status()['state'] == 'cancelling'
    finally:
        release.set()
        if closer:
            closer.join(3)
    assert finished(app)['state'] == 'cancelled'
    assert 'prepare' not in calls and 'activate' not in calls


def test_duplicate_conflicting_and_low_level_requests_cannot_change_active_selection(flow, monkeypatch):
    app, client, headers, _, _, _, _ = flow
    entered, release = threading.Event(), threading.Event()
    original = app.state.connection.inspect_install
    def blocked(**kw):
        entered.set()
        assert release.wait(3)
        return original(**kw)
    monkeypatch.setattr(app.state.connection, 'inspect_install', blocked)
    try:
        first = start(client, headers)
        assert entered.wait(1)
        assert start(client, headers).json()['operation_id'] == first.json()['operation_id']
        assert start(client, headers, request_id='different-request').status_code == 409
        assert start(client, headers, profile='h3-int8-group-offload-experimental').status_code == 409
        assert client.post(API + '/setup/plan', json={'profile': PROFILE}, headers=headers).status_code == 409
        assert client.post(API + '/setup/install', json={'plan_digest': 'a' * 64}, headers=headers).status_code == 409
    finally:
        release.set()
    assert finished(app)['state'] == 'completed'


def test_terminal_error_is_redacted_and_not_retried(flow, monkeypatch):
    app, client, headers, calls, _, _, _ = flow
    def denied(**kw):
        calls.append(kw['action'])
        raise ValueError('SSH-SECRET remote diagnostic')
    monkeypatch.setattr(app.state.connection, 'deployment_action', denied)
    assert start(client, headers).status_code == 202
    result = finished(app)
    assert result['state'] == 'failed' and result['error'] == 'setup_failed'
    assert 'SSH-SECRET' not in str(result)
    assert calls.count('snapshot') == 1 and 'prepare' not in calls


def test_cancel_only_download_owned_by_this_install(flow, monkeypatch):
    app, client, headers, calls, _, state, _ = flow
    original = app.state.connection.deployment_action
    captured = []
    def action(**kw):
        if kw['action'] == 'cancel-download':
            captured.append(kw['data'])
            return {'state': 'cancellation_requested'}
        result = original(**kw)
        if kw['action'] == 'download':
            state['operation']['state'] = 'running'
        return result
    monkeypatch.setattr(app.state.connection, 'deployment_action', action)
    assert start(client, headers).status_code == 202
    eventually(lambda: app.state.setup._download_operation is not None)
    assert client.post(API + '/setup/cancel', json={}, headers=headers).status_code == 202
    eventually(lambda: not app.state.setup.active())
    result = app.state.setup.status()
    assert result['state'] == 'cancelled'
    assert captured == [{'plan_digest': result['plan_digest'], 'operation': 'a' * 32}]
    assert not app.state.recovery.status()['credential_retained']


def test_plan_blockers_stop_before_even_testing_sudo(flow, monkeypatch):
    app, client, headers, calls, passwords, _, _ = flow
    monkeypatch.setattr(app.state.connection, 'inspect_install', lambda **kw: facts(issues=('Administrator repair required.',)))
    assert start(client, headers).status_code == 202
    result = finished(app)
    assert result['error'] == 'setup_plan_blocked'
    assert result['blockers'] == ['Administrator repair required.']
    assert not passwords and 'prepare' not in calls


def test_post_start_selection_drift_is_rejected_before_install(flow, monkeypatch):
    app, client, headers, calls, _, _, _ = flow
    original = app.state.deployment.make_plan
    def changed(*args, **kw):
        return original('h3-int8-group-offload-experimental', **kw)
    monkeypatch.setattr(app.state.deployment, 'make_plan', changed)
    assert start(client, headers).status_code == 202
    assert finished(app)['error'] == 'selection_changed'
    assert 'prepare' not in calls


def test_durable_install_marker_blocks_ambiguous_relaunch_even_new_coordinator(flow, monkeypatch):
    from h3server.controller.setup import SetupCoordinator
    app, client, headers, calls, _, _, _ = flow
    original = app.state.connection.recovery
    def failed(**kw):
        if kw['action'] == 'prepare':
            calls.append('prepare')
            raise ValueError('lost acceptance; do not replay')
        return original(**kw)
    monkeypatch.setattr(app.state.connection, 'recovery', failed)
    assert start(client, headers).status_code == 202
    assert finished(app)['state'] == 'failed'
    eventually(lambda: not app.state.setup.active())
    assert client.post(API + '/disconnect', json={}, headers=headers).status_code == 200
    assert connect(client, headers).status_code == 200
    assert client.post(API + '/authenticate', json={'password': 'SSH-SECRET', 'reuse_for_setup': True}, headers=headers).status_code == 200
    restarted = SetupCoordinator(app.state.deployment, app.state.recovery, lambda: app.state.session.active, poll_interval=.01)
    try:
        restarted.start(PROFILE, None, 'new-controller-request')
        eventually(lambda: not restarted.active())
        assert restarted.status()['error'] == 'setup_operation_outcome_unknown'
        assert calls.count('prepare') == 1
    finally:
        restarted.close()


def test_old_recovery_consent_does_not_silently_authorize_sudo(flow):
    app, client, headers, calls, _, _, _ = flow
    assert client.post(API + '/disconnect', json={}, headers=headers).status_code == 200
    assert connect(client, headers).status_code == 200
    assert client.post(API + '/authenticate', json={'password': 'SSH-SECRET', 'retain_for_setup_recovery': True}, headers=headers).status_code == 200
    assert app.state.recovery.status()['credential_retained']
    response = start(client, headers)
    assert response.status_code == 409 and response.json()['error']['code'] == 'setup_credential_required'
    assert not calls


def test_remote_progress_cursor_is_real_and_activation_failure_stops_download(flow, monkeypatch):
    app, client, headers, calls, _, state, _ = flow
    original = app.state.connection.deployment_action
    cursors = []
    def action(**kw):
        result = original(**kw)
        if kw['action'] == 'snapshot':
            cursors.append(kw['data']['after'])
            result['events'] = [{'stage': 'fixture', 'status': 'completed', 'message': 'Recorded remote event'}]
            result['cursor'] = len(cursors)
        elif kw['action'] == 'activate':
            state['operation']['state'] = 'failed'
        return result
    monkeypatch.setattr(app.state.connection, 'deployment_action', action)
    assert start(client, headers).status_code == 202
    result = finished(app)
    assert result['error'] == 'setup_operation_failed'
    assert cursors == list(range(len(cursors)))
    assert len(result['snapshot']['events']) == len(cursors)
    assert 'download' not in calls and not result['model_ready']


def test_target_change_resets_visible_progress_and_old_request_id_cannot_rebind(flow):
    app, client, headers, _, _, _, _ = flow
    assert start(client, headers).status_code == 202
    assert finished(app)['state'] == 'completed'
    eventually(lambda: not app.state.setup.active())
    assert client.post(API + '/disconnect', json={}, headers=headers).status_code == 200
    assert client.get(API + '/setup/status').json()['state'] == 'idle'
    result = client.post(API + '/connect', json={'host': 'other.example', 'username': 'other'}, headers=headers)
    assert result.status_code == 200
    response = start(client, headers)
    assert response.status_code == 409 and response.json()['error']['code'] == 'setup_request_conflict'


def test_prerequisite_replan_budget_is_checked_before_another_discovery(flow, monkeypatch):
    from h3server.controller.deploy import _write
    app, client, headers, calls, _, _, _ = flow
    inspections = []
    def inspect(**kw):
        version = len(inspections) + 1
        inspections.append(version)
        return facts(python_ready=False, packages=(f'python3-venv={version}',),
                     package_changes=(f'Inst python3-venv ({version} Ubuntu [amd64])',))
    def prerequisites(**kw):
        calls.append('prerequisites')
        with app.state.connection.private_sftp() as sftp:
            _write(sftp, f"{kw['root']}/bootstrap/{kw['approval'].plan_digest}/status", b'waiting_for_replan\n')
    monkeypatch.setattr(app.state.connection, 'inspect_install', inspect)
    monkeypatch.setattr(app.state.connection, 'install_prerequisites', prerequisites)
    assert start(client, headers).status_code == 202
    result = finished(app)
    assert result['error'] == 'setup_replan_limit'
    assert inspections == [1, 2, 3, 4, 5]  # initial discovery + at most four replans
    assert calls == ['prerequisites'] * 5


def test_reboot_is_not_reissued_when_remote_remains_waiting(flow, monkeypatch):
    app, client, headers, calls, _, state, _ = flow
    state['reboot'] = True
    app.state.setup.timeout = .15
    monkeypatch.setattr(app.state.connection, 'inspect_install', lambda **kw: facts(driver_working=False,
        driver_package='nvidia-driver-580', packages=('nvidia-driver-580=1',),
        package_changes=('Inst nvidia-driver-580 (1 Ubuntu [amd64])',)))
    original = app.state.connection.recovery
    def unchanged(**kw):
        result = original(**kw)
        if kw['action'] == 'reboot':
            state['setup'] = 'waiting_for_reboot'
        return result
    monkeypatch.setattr(app.state.connection, 'recovery', unchanged)
    assert start(client, headers).status_code == 202
    result = finished(app)
    assert result['error'] == 'setup_timeout'
    assert calls.count('reboot') == calls.count('prepare') == 1
    assert 'activate' not in calls
