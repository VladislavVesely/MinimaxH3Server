"""Selected authenticated routes replay real target diagnostics and safe retry state."""
from dataclasses import asdict
import io
import json
import os
import sys
import threading

import pytest

from h3server.controller.target import snapshot
from h3server.installer.diagnostics import Diagnostics, read_diagnostics
from h3server.installer.recovery import Recovery, service_plan
from h3server.installer.runner import Installer, InstallerBusy
from h3server.installer.steps import CommandFailure, ProcessIO, Steps
from h3server.store import Store, SetupState, canonical_plan_digest
from test_install_plan import approval
from test_installer import prepared
from test_recovery import SystemIO
from test_setup_coordinator import flow, start, finished, PROFILE
from test_controller_security import API, ORIGIN
from test_controller_reconnection import eventually


def failed_target(tmp_path, monkeypatch):
    paths, plan, store = prepared(tmp_path)
    system = SystemIO()
    recovery = Recovery(paths, store, io=system, uid=os.getuid())
    # Use real receipt/unit generation and stages; only OS/systemd commands mocked.
    original = system.run
    def run(argv, **kw):
        if argv[:2] == ('loginctl', 'show-user'):
            return 'yes\n'
        return original(argv, **kw)
    system.run = run
    runner = Installer(paths, store, io=system)
    setup = runner.create(plan)
    recovery.prepare(setup.setup_id, approval(plan, privileged=True),
                     service_digest=canonical_plan_digest(service_plan(paths, plan, uid=os.getuid())))
    def fail(argv, **kw):
        if 'pip' in argv and 'install' in argv and 'help' not in argv:
            return ProcessIO().run((sys.executable, '-c', "print('No matching distribution found for av==18.1.0'); raise SystemExit(1)"), **kw)
        return run(argv, **kw)
    system.run = fail
    monkeypatch.setattr('h3server.installer.steps.time.sleep', lambda seconds: threading.Event().wait(.001))
    with pytest.raises(CommandFailure):
        recovery.resume(setup.setup_id, setup.plan_digest)
    return paths, plan, store, setup, recovery, system, run


def test_real_failure_through_selected_routes_reload_and_explicit_retry(flow, tmp_path, monkeypatch):
    app, client, headers, calls, _, state, _ = flow
    paths, plan, store, setup, recovery, system, good = failed_target(tmp_path, monkeypatch)
    # Keep the route's actual selection/release binding, transport only is replaced.
    original_action = app.state.connection.deployment_action
    retry_calls = []
    def target(**kw):
        if kw['action'] == 'snapshot':
            document = snapshot(paths, Store(store.path), 0, setup.plan_digest)
            document['setups'][0]['plan_digest'] = kw['digest']
            document['diagnostics']['plan_digest'] = kw['digest']
            document['active_release'] = state['active_release']
            if state['operation'] is not None:
                document['operation'] = state['operation']
            return document
        if kw['action'] == 'retry-stage':
            retry_calls.append(kw)
            system.run = good
            recovery.retry_stage(setup.setup_id, setup.plan_digest)
            from h3server.backends.base import atomic_json
            atomic_json(paths, 'state/operation.json', {'operation': 'b' * 32,
                'action': 'retry-stage', 'state': 'completed', 'plan_digest': kw['digest']})
            return {'state': 'accepted', 'operation': 'b' * 32}
        return original_action(**kw)
    monkeypatch.setattr(app.state.connection, 'deployment_action', target)
    assert start(client, headers).status_code == 202
    result = finished(app)
    assert result['error'] == 'setup_install_failed'
    assert result['retry']['eligible']
    assert 'av==18.1.0' in result['diagnostics']['tail']
    assert result['diagnostics']['command']['exit_code'] == 1
    assert not retry_calls and 'prepare' not in calls
    app.state.setup.reset()
    assert client.post(API + '/setup/restore', json={}, headers=headers).status_code == 200
    replay = client.get(API + '/setup/events').json()
    assert 'av==18.1.0' in replay['diagnostics']['tail']
    body = {'profile': PROFILE, 'request_id': 'explicit-retry-one', 'plan_digest': app.state.deployment.document()['plan_digest']}
    assert client.post(API + '/setup/retry', json=body, headers={'origin': ORIGIN}).status_code == 403
    assert client.post(API + '/setup/retry', json=body, headers=headers).status_code == 202
    assert finished(app)['state'] == 'completed'
    assert len(retry_calls) == 1
    assert client.post(API + '/setup/retry', json=body, headers=headers).status_code == 202
    assert len(retry_calls) == 1
    assert 'prepare' not in calls


def test_retry_preserves_completed_privilege_and_checks_lock_receipt(tmp_path, monkeypatch):
    paths, plan, store, setup, recovery, system, good = failed_target(tmp_path, monkeypatch)
    failed = store.get_setup(setup.setup_id)
    assert recovery.retry_eligibility(failed)['eligible']
    before = list(system.calls)
    with Installer(paths, store).lock():
        assert recovery.retry_eligibility(failed)['reason'] == 'installer_busy'
        with pytest.raises(InstallerBusy):
            recovery.retry_stage(setup.setup_id, setup.plan_digest)
    unit = paths.config / 'systemd/h3-bootstrap.service'
    saved = unit.read_text()
    unit.write_text('changed')
    assert recovery.retry_eligibility(failed)['reason'] == 'recovery_receipt_invalid'
    unit.write_text(saved)
    system.run = good
    recovery.retry_stage(setup.setup_id, setup.plan_digest)
    assert store.get_setup(setup.setup_id).state == SetupState.READY
    assert all(argv[0] != 'sudo' and 'venv' not in argv for argv, _ in system.calls[len(before):])
    assert not recovery.retry_eligibility(store.get_setup(setup.setup_id))['eligible']


@pytest.mark.parametrize('message,attempts,fresh', [
    ('No matching distribution found for av==18.1.0', 2, True),
    ('ReadTimeoutError: connection timed out', 3, False),
    ('ResolutionImpossible: conflicting dependencies', 1, False),
    ('requires a different Python', 1, False),
])
def test_acquisition_retry_budget_and_exact_pins(tmp_path, monkeypatch, message, attempts, fresh):
    paths, plan, store = prepared(tmp_path)
    calls, delays = [], []
    class Fail:
        def run(self, argv, **kw):
            calls.append(argv)
            raise CommandFailure(message)
    monkeypatch.setattr('h3server.installer.steps.time.sleep', delays.append)
    steps = Steps(paths, plan, canonical_plan_digest(plan), Fail(), io.StringIO())
    command = (steps.python, '-m', 'pip', 'install', '--only-binary=:all:', '--index-url', 'https://pypi.org/simple', 'av==18.1.0')
    with pytest.raises(CommandFailure):
        steps.acquire(*command)
    assert len(calls) == attempts
    assert all(c[:len(command)] == command for c in calls)
    assert ('--no-cache-dir' in calls[-1]) is fresh
    assert len(delays) == attempts - 1


def test_live_diagnostics_visible_while_child_is_running(tmp_path):
    paths, plan, store = prepared(tmp_path)
    setup = store.create_setup(plan)
    log = Diagnostics(paths, setup.setup_id, setup.plan_digest)
    log.phase('dependencies', 'python-packages', 'Install av==18.1.0')
    errors = []
    def child():
        try:
            ProcessIO(command_timeout=.8).run((sys.executable, '-u', '-c', "import time; print('Downloading av==18.1.0'); time.sleep(30)"), env={}, log=log)
        except CommandFailure as error:
            errors.append(error)
    thread = threading.Thread(target=child)
    thread.start()
    try:
        eventually(lambda: 'Downloading' in read_diagnostics(paths, setup)['tail'])
        command = read_diagnostics(paths, setup)['command']
        assert command['last_output_at'] and command['ended_at'] is None
    finally:
        thread.join(3)
    assert errors and not thread.is_alive()


@pytest.mark.parametrize('help_text,expected', [('choices: auto, on, off, raw', 'raw'), ('choices: on, off', 'off')])
def test_pip_progress_capability_uses_raw_bytes_or_verbose_fallback(tmp_path, help_text, expected):
    paths, plan, store = prepared(tmp_path)
    calls = []
    class IO:
        def run(self, argv, **kw):
            calls.append(argv)
            return help_text if 'help' in argv else ''
    steps = Steps(paths, plan, canonical_plan_digest(plan), IO(), io.StringIO())
    steps.pip_install('--only-binary=:all:', 'av==18.1.0')
    assert calls[-1][calls[-1].index('--progress-bar') + 1] == expected
    assert '--verbose' in calls[-1] and 'av==18.1.0' in calls[-1]
    assert '--only-binary=:all:' in calls[-1]


def test_real_process_transient_failure_then_same_pins_success(tmp_path, monkeypatch):
    paths, plan, store = prepared(tmp_path)
    setup = store.create_setup(plan)
    log = Diagnostics(paths, setup.setup_id, setup.plan_digest)
    log.phase('dependencies', 'python-packages', 'Install av==18.1.0')
    marker = tmp_path / 'attempt'
    script = ("from pathlib import Path; import sys; p=Path(sys.argv[1]); first=not p.exists(); "
              "p.touch(); print('ReadTimeoutError: connection timed out' if first else 'installed av==18.1.0'); "
              "raise SystemExit(1 if first else 0)")
    monkeypatch.setattr('h3server.installer.steps.time.sleep', lambda seconds: None)
    class LocalProcess:
        def run(self, argv, **kw):
            assert argv[-1] == 'av==18.1.0'
            return ProcessIO().run((sys.executable, '-c', script, str(marker)), **kw)
    steps = Steps(paths, plan, setup.plan_digest, LocalProcess(), log)
    steps.acquire(steps.python, '-m', 'pip', 'install', 'av==18.1.0')
    record = read_diagnostics(paths, setup)
    assert record['command']['exit_code'] == 0
    assert record['retry']['attempt'] == 1
    assert record['retry']['reason'] == 'transient-download'
    assert 'installed av==18.1.0' in record['tail']


@pytest.mark.parametrize('command', [('sudo', '-n', 'apt-get', 'update'), ('python', '-m', 'venv', '/tmp/no-write')])
def test_retry_boundary_rejects_privileged_or_environment_commands(tmp_path, command):
    paths, plan, store = prepared(tmp_path)
    steps = Steps(paths, plan, canonical_plan_digest(plan), ProcessIO(), io.StringIO())
    with pytest.raises(ValueError, match='only fixed dependency'):
        steps.acquire(*command)


@pytest.mark.parametrize('outcome', ['completed', 'interrupted', 'failed', 'ownership-changed', 'lost-acceptance', 'cancelled'])
def test_async_retry_waits_for_exact_completed_operation_and_lock_release(flow, tmp_path, monkeypatch, outcome):
    from h3server.backends.base import atomic_json
    from h3server.controller.target import operation_lock
    app, client, headers, calls, _, state, _ = flow
    paths, plan, store, setup, recovery, system, good = failed_target(tmp_path, monkeypatch)
    original = app.state.connection.deployment_action
    ready, finish, written, release = (threading.Event() for _ in range(4))
    errors, workers = [], []
    operation_id = 'b' * 32
    def target(**kw):
        if kw['action'] == 'snapshot':
            document = snapshot(paths, Store(store.path), 0, setup.plan_digest)
            document['setups'][0]['plan_digest'] = kw['digest']
            document['diagnostics']['plan_digest'] = kw['digest']
            document['active_release'] = state['active_release']
            if state['operation'] is not None:
                document['operation'] = state['operation']
            return document
        if kw['action'] == 'retry-stage':
            def worker():
                try:
                    with operation_lock(paths):
                        record = {'operation': operation_id, 'action': 'retry-stage',
                                  'state': 'running', 'plan_digest': kw['digest']}
                        atomic_json(paths, 'state/operation.json', record)
                        system.run = good
                        recovery.retry_stage(setup.setup_id, setup.plan_digest)
                        assert store.get_setup(setup.setup_id).state == SetupState.READY
                        ready.set()
                        assert finish.wait(5)
                        if outcome != 'interrupted':
                            record['state'] = 'failed' if outcome == 'failed' else 'completed'
                            if outcome == 'ownership-changed':
                                record['operation'] = 'c' * 32
                            atomic_json(paths, 'state/operation.json', record)
                        written.set()
                        assert release.wait(5)
                except BaseException as error:
                    errors.append(error)
            thread = threading.Thread(target=worker)
            workers.append(thread); thread.start()
            assert ready.wait(3)
            return None if outcome == 'lost-acceptance' else {'state': 'accepted', 'operation': operation_id}
        if kw['action'] == 'activate':
            with operation_lock(paths):  # A premature activation really collides.
                return original(**kw)
        return original(**kw)
    monkeypatch.setattr(app.state.connection, 'deployment_action', target)
    assert start(client, headers).status_code == 202
    assert finished(app)['error'] == 'setup_install_failed'
    body = {'profile': PROFILE, 'plan_digest': app.state.deployment.document()['plan_digest'],
            'request_id': 'async-retry-operation'}
    try:
        assert client.post(API + '/setup/retry', json=body, headers=headers).status_code == 202
        assert ready.wait(3)
        if outcome == 'lost-acceptance':
            assert finished(app)['error'] == 'setup_operation_outcome_unknown'
            assert 'activate' not in calls
            assert len(workers) == 1  # Never replay an uncertain retry acceptance.
            return
        eventually(lambda: app.state.setup.status().get('snapshot', {}).get('setups', [{}])[0].get('state') == 'ready')
        threading.Event().wait(.05)
        assert app.state.setup.status()['state'] == 'running'
        assert 'activate' not in calls
        if outcome == 'cancelled':
            assert client.post(API + '/setup/cancel', json={}, headers=headers).status_code == 202
            assert finished(app)['state'] == 'cancelled'
            assert 'activate' not in calls
            return
        finish.set(); assert written.wait(3)
        if outcome == 'completed':
            eventually(lambda: app.state.setup.status()['snapshot']['operation']['state'] == 'completed')
            threading.Event().wait(.05)
            assert app.state.setup.status()['state'] == 'running'
            assert 'activate' not in calls
        release.set()
        result = finished(app)
        if outcome == 'completed':
            assert result['state'] == 'completed', result
            assert calls.count('activate') == 1
        else:
            assert result['state'] == 'failed', result
            assert 'activate' not in calls
            assert result['error'] == ('setup_remote_busy' if outcome == 'ownership-changed' else 'setup_operation_failed')
        assert 'prepare' not in calls
    finally:
        finish.set(); release.set()
        for worker in workers:
            worker.join(5)
        assert not errors


@pytest.mark.parametrize('change', ['completed', 'ownership', 'missing', 'malformed'])
def test_snapshot_rereads_operation_under_lock_before_reporting_release(tmp_path, monkeypatch, change):
    from contextlib import contextmanager
    from h3server.backends.base import atomic_json
    from h3server.controller import target
    paths, plan, store = prepared(tmp_path)
    record = {'operation': 'a' * 32, 'action': 'retry-stage', 'state': 'running',
              'plan_digest': canonical_plan_digest(plan)}
    atomic_json(paths, 'state/operation.json', record)
    original = target.operation_lock
    @contextmanager
    def raced_lock(paths):
        with original(paths) as fd:
            if change == 'missing':
                (paths.state / 'operation.json').unlink()
            elif change == 'malformed':
                (paths.state / 'operation.json').write_text('{')
            else:
                atomic_json(paths, 'state/operation.json', {**record, 'state': 'completed',
                    'operation': 'b' * 32 if change == 'ownership' else record['operation']})
            yield fd
    monkeypatch.setattr(target, 'operation_lock', raced_lock)
    result = snapshot(paths, store, 0)['operation']
    assert result['state'] != 'interrupted'
    assert result['lock_released'] is (change == 'completed')


def test_retry_wait_without_release_metadata_times_out_closed(flow, monkeypatch):
    from h3server.controller.ssh import ControllerError
    app, _, _, _, _, _, _ = flow
    coordinator = app.state.setup
    digest, setup_id, operation_id = 'a' * 64, 'b' * 32, 'c' * 32
    snapshot = {'setups': [{'plan_digest': digest, 'setup_id': setup_id, 'state': 'ready'}],
        'operation': {'plan_digest': digest, 'operation': operation_id, 'state': 'completed', 'action': 'retry-stage'}}
    clock = [0]
    monkeypatch.setattr('h3server.controller.setup.time.monotonic', lambda: clock[0])
    monkeypatch.setattr(coordinator, '_snapshot', lambda: snapshot)
    monkeypatch.setattr(coordinator, '_wait', lambda: clock.__setitem__(0, clock[0] + 31))
    with pytest.raises(ControllerError, match='setup_operation_outcome_unknown'):
        coordinator._await_operation('retry-stage', digest, operation_id, expected_setup_id=setup_id)
