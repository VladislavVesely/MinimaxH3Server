"""Saved setup inspection is read-only, including after fresh authentication."""
import pytest

from test_setup_coordinator import flow, PROFILE
from test_controller_security import API


def test_fresh_authenticated_restore_without_record_is_precise(flow):
    app, client, headers, calls, _, _, _ = flow
    app.state.deployment.reset()
    response = client.post(API + '/setup/restore', json={}, headers=headers)
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'setup_not_found'
    assert app.state.deployment.root == '/home/test/MinimaxH3Server'
    assert not calls


def test_restore_same_release_observes_without_service_command(flow):
    app, client, headers, calls, _, _, _ = flow
    document = app.state.deployment.make_plan(PROFILE)
    app.state.deployment.reset()
    calls.clear()
    response = client.post(API + '/setup/restore', json={}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()['plan_digest'] == document['plan_digest']
    assert response.json()['service'] is None
    assert not calls


def test_restore_prerequisites_never_replans(flow, monkeypatch):
    from test_install_plan import facts
    app, client, headers, calls, _, _, sftp = flow
    monkeypatch.setattr(app.state.connection, 'inspect_install', lambda **kw: facts(
        python_ready=False, packages=('python3-venv=1',),
        package_changes=('Inst python3-venv (1 Ubuntu [amd64])',)))
    document = app.state.deployment.make_plan(PROFILE, sudo_password_available=True)
    status = sftp.path(app.state.deployment.root + '/bootstrap/' + document['plan_digest'] + '/status')
    status.write_text('waiting_for_replan\n'); status.chmod(0o600)
    app.state.deployment.reset()
    calls.clear()
    monkeypatch.setattr(app.state.deployment, 'make_plan', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('observation replanned')))
    response = client.post(API + '/setup/restore', json={}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()['plan_digest'] == document['plan_digest']
    assert not calls


def test_phase_elapsed_changes_only_on_transition_and_freezes_terminal(flow, monkeypatch):
    app, _, _, _, _, _, _ = flow
    clock = [100.0]
    monkeypatch.setattr('h3server.controller.setup.time.monotonic', lambda: clock[0])
    coordinator = app.state.setup
    assert coordinator.status()['phase_elapsed_seconds'] is None
    coordinator._publish(state='running', phase='installing')
    clock[0] = 107.0
    coordinator._publish(phase='installing', diagnostics=None)
    assert coordinator.status()['phase_elapsed_seconds'] == 7
    coordinator._publish(phase='downloading')
    assert coordinator.status()['phase_elapsed_seconds'] == 0
    clock[0] = 110.0
    coordinator._publish(state='failed')
    clock[0] = 120.0
    assert coordinator.status()['phase_elapsed_seconds'] == 3
    coordinator.reset()
    assert coordinator.status()['phase_elapsed_seconds'] is None


@pytest.mark.parametrize('terminal', ['completed', 'failed', 'cancelled'])
def test_phase_timer_freezes_all_terminal_states(flow, monkeypatch, terminal):
    app, _, _, _, _, _, _ = flow
    clock = [100.0]
    monkeypatch.setattr('h3server.controller.setup.time.monotonic', lambda: clock[0])
    coordinator = app.state.setup
    coordinator._publish(state='running', phase='installing')
    clock[0] = 105.0
    coordinator._publish(state=terminal)
    clock[0] = 200.0
    assert coordinator.status()['phase_elapsed_seconds'] == 5


@pytest.mark.parametrize('damage', ['missing-plan', 'malformed-plan', 'malformed-pointer'])
def test_saved_record_damage_is_not_mistaken_for_absent_setup(flow, damage):
    app, client, headers, calls, _, _, sftp = flow
    document = app.state.deployment.make_plan(PROFILE)
    root = app.state.deployment.root
    plan = sftp.path(root + '/bootstrap/' + document['plan_digest'] + '/plan.json')
    if damage == 'missing-plan':
        plan.unlink()
    elif damage == 'malformed-plan':
        plan.write_text('{')
    else:
        sftp.path(root + '/config/deployment.json').write_text('{}')
    app.state.deployment.reset()
    calls.clear()
    response = client.post(API + '/setup/restore', json={}, headers=headers)
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'invalid_checkpoint'
    assert app.state.deployment.plan is None
    assert not calls


def test_inspect_unlaunched_environment_then_explicit_install_loads_service_once(flow):
    from test_setup_coordinator import start, finished
    app, client, headers, calls, _, _, _ = flow
    document = app.state.deployment.make_plan(PROFILE)
    calls.clear()
    observed = client.post(API + '/setup/restore', json={}, headers=headers)
    assert observed.status_code == 200
    assert observed.json()['service'] is None
    assert not calls  # Inspection is still read-only, without service-plan execution.
    assert start(client, headers).status_code == 202
    status = finished(app)
    assert status['state'] == 'completed', status
    assert status['plan_digest'] == document['plan_digest']
    assert calls == ['service-plan', 'prepare', 'activate', 'download']
    assert start(client, headers).status_code == 202
    assert calls.count('prepare') == 1


def test_inspected_plan_service_observation_failure_never_writes_launch_marker(flow, monkeypatch):
    from h3server.probe import CommandResult
    from test_setup_coordinator import start, finished
    app, client, headers, calls, _, _, sftp = flow
    document = app.state.deployment.make_plan(PROFILE)
    assert client.post(API + '/setup/restore', json={}, headers=headers).status_code == 200
    calls.clear()
    def unavailable(**kw):
        calls.append(kw['action'])
        assert kw['action'] == 'service-plan'
        assert kw['digest'] == document['plan_digest']
        assert kw['artifact'] == app.state.deployment.release.artifact
        return CommandResult(returncode=1)
    monkeypatch.setattr(app.state.connection, 'recovery', unavailable)
    assert start(client, headers).status_code == 202
    assert finished(app)['state'] == 'failed'
    assert calls == ['service-plan']
    stage = sftp.path(app.state.deployment.root + '/bootstrap/' + document['plan_digest'])
    assert not (stage / 'composite-install.json').exists()
    assert not (stage / 'approval.json').exists()
