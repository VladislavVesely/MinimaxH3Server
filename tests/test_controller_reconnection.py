"""Controller recovery is observation-only and bound to the authenticated target."""
from contextlib import nullcontext
import threading
import time
from types import SimpleNamespace

import pytest

from h3server.controller.ssh import ControllerError
from h3server.store import canonical_plan_digest


def eventually(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


class Connection:
    def __init__(self):
        self.state = 'authenticated'
        self.calls = []
        self.failures = []
        self.lock = threading.RLock()

    def deployment_deadline(self, timeout):
        return nullcontext()

    def close(self):
        self.state = 'disconnected'

    def status(self):
        return {'state': self.state}

    def connect(self, *target, timeout):
        self.calls.append(('connect', target))
        if self.failures:
            raise ControllerError(self.failures.pop(0))
        self.state = 'awaiting_authentication'
        return self.status()

    def authenticate(self, password, timeout):
        self.calls.append(('authenticate',))
        self.state = 'authenticated'


@pytest.fixture
def recovery():
    from h3server.controller.reconnection import SetupRecovery
    connection = Connection()
    deployment = SimpleNamespace(plan={'purpose': 'environment', 'stages': ['environment']}, root='/home/test/MinimaxH3Server')
    calls = []
    deployment.restore = lambda **kw: calls.append(kw)
    active = [True]
    manager = SetupRecovery(connection, deployment, lambda: active[0], timeout=1)
    manager.set_target(('example.org', 22, 'test'))
    manager.remember('SECRET', enabled=True)
    yield manager, connection, deployment, calls, active
    manager.close()


def test_transient_recovery_is_bound_and_observation_only(recovery):
    manager, connection, deployment, calls, _ = recovery
    connection.failures = ['connection_failed']
    assert manager.start()
    assert not manager.start()  # exactly one worker
    eventually(lambda: manager.status()['state'] == 'recovered')
    assert calls == [{'expected_digest': canonical_plan_digest(deployment.plan),
                      'expected_root': deployment.root, 'observation_only': True}]
    assert connection.calls == [('connect', ('example.org', 22, 'test')),
                                ('connect', ('example.org', 22, 'test')), ('authenticate',)]
    assert 'SECRET' not in repr(manager) + str(manager.status())


@pytest.mark.parametrize('error', ['authentication_failed', 'host_key_changed', 'trust_required'])
def test_security_failures_stop_and_clear_credential(recovery, error):
    manager, connection, _, calls, _ = recovery
    connection.failures = [error]
    manager.start()
    eventually(lambda: manager.status()['state'] == 'failed')
    assert manager.status()['error'] == error
    assert not manager.status()['credential_retained']
    assert len(connection.calls) == 1 and not calls


def test_cancel_switch_optout_expiry_cleanup(recovery):
    manager, connection, _, calls, active = recovery
    connection.failures = ['connection_failed'] * 20
    manager.start()
    eventually(lambda: manager.status()['state'] == 'backoff')
    manager.clear()
    assert not manager.status()['credential_retained']
    assert not manager.start() and not calls
    manager.set_target(('other.example', 22, 'test'))
    assert not manager.start()
    manager.remember('SECRET', enabled=True)
    manager.remember('SECRET', enabled=False)
    assert not manager.start()
    manager.remember('SECRET', enabled=True)
    active[0] = False
    manager.expire()
    assert not manager.status()['credential_retained']


def test_recovery_deadline_is_terminal(recovery):
    manager, connection, _, calls, _ = recovery
    connection.failures = ['connection_failed'] * 20
    manager.start()
    eventually(lambda: manager.status()['state'] == 'failed')
    assert manager.status()['error'] == 'reconnect_timeout'
    assert not calls and not manager.start()


def test_restore_mismatch_fails_closed(recovery):
    manager, connection, deployment, _, _ = recovery
    def mismatch(**kw):
        raise ControllerError('recovery_binding_changed')
    deployment.restore = mismatch
    manager.start()
    eventually(lambda: manager.status()['state'] == 'failed')
    assert manager.status()['error'] == 'recovery_binding_changed'
    assert connection.state == 'disconnected'


@pytest.fixture
def production(tmp_path, monkeypatch):
    """Real routes, SSH trust/auth and Deployment; fixture target command/SFTP IO."""
    import json
    from fastapi.testclient import TestClient
    from h3server.controller.app import create_app
    from h3server.probe import CommandResult
    from test_controller_security import API, ORIGIN, bootstrap, connect
    from test_deploy import bundle, LocalSFTP
    from test_install_plan import facts
    from test_ssh import Network
    network = Network()
    app = create_app(state_dir=tmp_path / 'trust', origin=ORIGIN, release=bundle(tmp_path),
                     socket_factory=network.socket, transport_factory=network.transport)
    connection = app.state.connection
    sftp = LocalSFTP(tmp_path / 'remote'); sftp.base.mkdir()
    monkeypatch.setattr(connection, 'open_sftp', lambda: sftp)
    monkeypatch.setattr(connection, '_staging_transaction', sftp.staging_transaction)
    monkeypatch.setattr(connection, 'inspect_install', lambda **kw: facts(driver_working=False,
        driver_package='nvidia-driver-580', packages=('nvidia-driver-580=1',),
        package_changes=('Inst nvidia-driver-580 (1 Ubuntu [amd64])',)))
    calls = []
    def recovery(**kw):
        connection._require_authentication()
        calls.append(kw['action'])
        if kw['action'] == 'service-plan':
            return CommandResult(json.dumps({'stages': ['services']}), returncode=0)
        assert kw['action'] == 'reboot'  # automatic resume/install is forbidden
        connection.close()
        return CommandResult(returncode=0)
    monkeypatch.setattr(connection, 'recovery', recovery)
    with TestClient(app, base_url=ORIGIN) as client:
        headers = bootstrap(app, client)
        fingerprint = connect(client, headers).json()['fingerprint']
        client.post(API + '/approve', json={'fingerprint': fingerprint}, headers=headers)
        client.post(API + '/authenticate', json={'password': 'SSH-SECRET',
            'retain_for_setup_recovery': True}, headers=headers)
        review = client.post(API + '/setup/plan', json={'profile': 'h3-bf16-cpu-offload-experimental'}, headers=headers)
        assert review.status_code == 200, review.text
        yield app, client, network, headers, review.json(), calls, sftp
    assert not app.state.recovery.status()['credential_retained']


def test_approved_reboot_route_recovers_same_checkpoint_without_reissuing(production):
    from test_controller_security import API
    app, client, network, headers, review, calls, _ = production
    request = {'plan_digest': review['plan_digest'], 'license_acknowledged': True,
               'eligibility_confirmed': True, 'privileged': True, 'reboot': False}
    assert client.post(API + '/setup/reboot', json=request, headers=headers).status_code == 409
    assert 'reboot' not in calls
    request.update(reboot=True, sudo_password='SUDO-NEVER-RETAINED')
    response = client.post(API + '/setup/reboot', json=request, headers=headers)
    assert response.status_code == 200, response.text
    assert not response.json()['reauthentication_required']
    eventually(lambda: app.state.recovery.status()['state'] == 'recovered')
    assert app.state.deployment.document()['plan_digest'] == review['plan_digest']
    assert calls == ['service-plan', 'reboot', 'service-plan']
    assert len(network.auth_calls) == 2
    assert all(call[1] == 'SSH-SECRET' for call in network.auth_calls)
    assert 'SUDO-NEVER-RETAINED' not in str(app.state.recovery.status())


@pytest.mark.parametrize('failure', ['authentication_failed', 'host_key_changed', 'trust_required'])
def test_production_recovery_stops_on_trust_or_authentication_failure(production, failure):
    import paramiko
    app, _, network, _, _, calls, _ = production
    if failure == 'authentication_failed':
        network.auth_error = paramiko.AuthenticationException('SSH-SECRET')
    elif failure == 'host_key_changed':
        network.key = paramiko.RSAKey.generate(1024)
    else:
        app.state.connection._store.path.unlink()
    app.state.connection.close()
    app.state.recovery.observe()
    eventually(lambda: app.state.recovery.status()['state'] == 'failed')
    assert app.state.recovery.status()['error'] == failure
    assert not app.state.recovery.status()['credential_retained']
    assert calls == ['service-plan']
    assert len(network.auth_calls) == (2 if failure == 'authentication_failed' else 1)


def test_production_restore_rejects_changed_digest_before_loading_new_plan(production):
    import json
    app, _, _, _, review, calls, sftp = production
    record_path = sftp.path(app.state.deployment.root + '/config/deployment.json')
    record = json.loads(record_path.read_text()); record['digest'] = 'a' * 64
    record_path.write_text(json.dumps(record))
    app.state.connection.close()
    app.state.recovery.observe()
    eventually(lambda: app.state.recovery.status()['state'] == 'failed')
    assert app.state.recovery.status()['error'] == 'recovery_binding_changed'
    assert calls == ['service-plan']
    assert app.state.deployment.document()['plan_digest'] == review['plan_digest']


def test_transient_setup_route_loss_starts_recovery_and_returns_only_safe_status(production, monkeypatch):
    from test_controller_security import API
    app, client, _, _, _, calls, _ = production
    def lost(**kw):
        app.state.connection.close()
        raise ControllerError('deployment_operation_failed', 502)
    monkeypatch.setattr(app.state.connection, 'deployment_action', lost)
    assert client.get(API + '/setup/events').status_code == 502
    eventually(lambda: app.state.recovery.status()['state'] == 'recovered')
    result = client.get(API + '/status').json()
    assert result['recovery']['state'] == 'recovered'
    assert 'SSH-SECRET' not in str(result)
    assert calls == ['service-plan', 'service-plan']


def test_target_switch_joins_old_worker_before_new_target_can_be_used(recovery):
    manager, connection, _, calls, _ = recovery
    entered, release, switched = threading.Event(), threading.Event(), threading.Event()
    def authenticate(password, timeout):
        entered.set()
        assert release.wait(2)
        connection.state = 'authenticated'
    connection.authenticate = authenticate
    manager.start()
    assert entered.wait(1)
    def switch():
        manager.set_target(('other.example', 22, 'new-user'))
        switched.set()
    thread = threading.Thread(target=switch); thread.start()
    try:
        assert not switched.wait(.05)
        assert manager._cancel.is_set()
    finally:
        release.set(); thread.join(2)
    assert switched.is_set() and not calls
    assert connection.state == 'disconnected'
    assert not manager.start() and not manager.status()['credential_retained']


@pytest.mark.parametrize('failure', [ControllerError('sftp_unavailable'), EOFError('SSH-SECRET')])
def test_restore_transport_loss_retries_observation_only(recovery, failure):
    manager, connection, deployment, calls, _ = recovery
    attempts = []
    def restore(**kw):
        attempts.append(kw)
        if len(attempts) == 1:
            connection.close()
            raise failure
        calls.append(kw)
    deployment.restore = restore
    manager.start()
    eventually(lambda: manager.status()['state'] == 'recovered')
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert len(calls) == 1


def test_production_restore_rejects_changed_home(production, monkeypatch):
    app, _, _, _, _, calls, _ = production
    monkeypatch.setattr(app.state.connection, 'remote_home', lambda: '/home/other')
    app.state.connection.close()
    app.state.recovery.observe()
    eventually(lambda: app.state.recovery.status()['state'] == 'failed')
    assert app.state.recovery.status()['error'] == 'recovery_binding_changed'
    assert calls == ['service-plan']


def test_reboot_after_optout_requires_manual_authentication(production):
    from test_controller_security import API
    app, client, network, headers, review, calls, _ = production
    assert client.post(API + '/recovery/forget', json={}, headers=headers).status_code == 200
    response = client.post(API + '/setup/reboot', json={
        'plan_digest': review['plan_digest'], 'license_acknowledged': True,
        'eligibility_confirmed': True, 'privileged': True, 'reboot': True}, headers=headers)
    assert response.status_code == 200
    assert response.json()['reauthentication_required']
    app.state.recovery.observe()
    assert not app.state.recovery.active()
    assert len(network.auth_calls) == 1 and calls == ['service-plan', 'reboot']
