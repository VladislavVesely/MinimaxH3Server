"""Recovery fixtures exercise actual composition/state, never reboot this machine."""
import io
import json
from dataclasses import asdict
from pathlib import Path
import subprocess
import threading

import pytest

from h3server.installer import recovery
from h3server.installer.runner import Installer
from h3server.installer.steps import CommandFailure
from h3server.store import ApprovalRequired, SetupState, canonical_plan_digest
from h3server.controller.ssh import ControllerError
from test_installer import prepared, FakeIO, AptIO
from test_install_plan import approval

BOOT_A = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
BOOT_B = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'


class SystemIO(FakeIO):
    def run(self, argv, **kw):
        if argv[:3] == ('loginctl', 'show-user', '1000'):
            self.calls.append((argv, kw['env']))
            return 'yes\n'
        return super().run(argv, **kw)


def service_consent(paths, p):
    document = recovery.service_plan(paths, p, uid=1000)
    return document, canonical_plan_digest(document)


def test_units_are_user_only_confined_and_escaped(tmp_path):
    paths, p, store = prepared(tmp_path / 'space % dollar$ quote"')
    document, digest = service_consent(paths, p)
    units = recovery.render_units(paths, p)
    assert set(units) == {'h3-bootstrap.service', 'h3server.service'}
    bootstrap, daemon = units.values()
    assert 'h3server.installer.recovery' in bootstrap
    assert 'h3server.remote --root' in daemon
    assert '%%' in daemon and '$$' in daemon and '\\"' in daemon
    assert 'User=root' not in bootstrap + daemon and 'sudo' not in bootstrap + daemon
    assert 'WantedBy=default.target' in daemon
    assert 'PYTHONPATH=' in bootstrap and '--digest ' + canonical_plan_digest(p) in bootstrap
    assert document['plan_digest'] == canonical_plan_digest(p)
    assert document['uid'] == 1000 and document['linger'] is True
    assert 'loopback' in document['daemon_contract']
    assert digest != canonical_plan_digest(p)


def test_prepare_requires_separate_service_approval_and_finishes_privilege_first(tmp_path):
    paths, p, store = prepared(tmp_path)
    io = SystemIO()
    r = recovery.Recovery(paths, store, io=io, uid=1000, boot_id=lambda: BOOT_A)
    setup = Installer(paths, store, io=io).create(p)
    doc, digest = service_consent(paths, p)
    with pytest.raises(ApprovalRequired):
        r.prepare(setup.setup_id, approval(p), service_digest='bad')
    assert not io.calls
    result = r.prepare(setup.setup_id, approval(p, privileged=True), service_digest=digest)
    assert result.state == SetupState.RUNNING
    calls = [argv for argv, _ in io.calls]
    assert ('sudo', '-n', 'loginctl', 'enable-linger', '1000') in calls
    assert ('systemctl', '--user', 'enable', str(paths.config / 'systemd/h3-bootstrap.service')) in calls
    assert not any('pip' in argv or 'h3server.remote' in argv for argv in calls)
    saved = json.loads((paths.bootstrap / setup.plan_digest / 'recovery.json').read_text())
    assert saved['service_digest'] == digest
    assert saved['approval'] == asdict(approval(p, privileged=True))
    recovery.Recovery(paths, StoreAgain(store), io=io, uid=1000, boot_id=lambda: BOOT_A).resume(setup.setup_id, setup.plan_digest)
    assert store.get_setup(setup.setup_id).state == SetupState.READY
    before = len(io.calls)
    r.resume(setup.setup_id, setup.plan_digest)
    assert len(io.calls) == before
    assert not any(argv[0] == 'sudo' for argv, _ in io.calls[len(calls):])


def StoreAgain(store):
    from h3server.store import Store
    return Store(store.path)


def test_boot_change_required_no_duplicate_reboot_or_installer(tmp_path):
    paths, p, store = prepared(tmp_path, driver_working=False, driver_package='nvidia-driver-580',
                              packages=('nvidia-driver-580=1',), package_changes=('Inst nvidia-driver-580 (1 Ubuntu [amd64])',))
    class IO(AptIO, SystemIO):
        pass
    io = IO(simulation='Inst nvidia-driver-580 (1 Ubuntu [amd64])')
    boot = [BOOT_A]
    r = recovery.Recovery(paths, store, io=io, uid=1000, boot_id=lambda: boot[0])
    setup = Installer(paths, store, io=io).create(p)
    _, digest = service_consent(paths, p)
    r.prepare(setup.setup_id, approval(p, privileged=True), service_digest=digest)
    assert r.resume(setup.setup_id, setup.plan_digest).state == SetupState.WAITING_FOR_REBOOT
    with pytest.raises(ApprovalRequired):
        r.request_reboot(setup.setup_id, approval(p, privileged=True))
    r.request_reboot(setup.setup_id, approval(p, privileged=True, reboot=True))
    r.request_reboot(setup.setup_id, approval(p, privileged=True, reboot=True))
    assert sum(argv == ('sudo', '-n', 'systemctl', 'reboot') for argv, _ in io.calls) == 1
    assert r.resume(setup.setup_id, setup.plan_digest).state == SetupState.WAITING_FOR_REBOOT
    boot[0] = BOOT_B
    io.driver = True
    assert r.resume(setup.setup_id, setup.plan_digest).state == SetupState.READY
    assert sum('-y' in argv for argv, _ in io.calls) == 1
    snapshot = r.snapshot(after=0)
    assert snapshot['setups'][0]['state'] == 'ready' and snapshot['events']
    assert r.snapshot(after=snapshot['cursor'])['events'] == []


def test_failed_driver_after_reboot_is_manual_not_reboot_loop(tmp_path):
    paths, p, store = prepared(tmp_path, driver_working=False, driver_package='nvidia-driver-580',
                              packages=('nvidia-driver-580=1',), package_changes=('Inst nvidia-driver-580 (1 Ubuntu [amd64])',))
    class IO(AptIO, SystemIO):
        pass
    io = IO(simulation='Inst nvidia-driver-580 (1 Ubuntu [amd64])')
    boot = [BOOT_A]
    r = recovery.Recovery(paths, store, io=io, uid=1000, boot_id=lambda: boot[0])
    setup = Installer(paths, store, io=io).create(p)
    r.prepare(setup.setup_id, approval(p, privileged=True), service_digest=service_consent(paths, p)[1])
    r.resume(setup.setup_id, setup.plan_digest)
    boot[0] = BOOT_B
    with pytest.raises(CommandFailure, match='manual'):
        r.resume(setup.setup_id, setup.plan_digest)
    assert store.get_setup(setup.setup_id).state == SetupState.FAILED
    with pytest.raises(CommandFailure, match='manual'):
        r.resume(setup.setup_id, setup.plan_digest)
    assert not any('reboot' in argv for argv, _ in io.calls)


def test_sudo_password_stdin_only_and_all_output_suppressed(tmp_path):
    paths, p, _ = prepared(tmp_path)
    calls = []
    secret = 'do-not-persist'
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 1, secret, secret)
    adapter = recovery.PrivilegedIO(p, uid=1000, password=secret, run=run)
    log = io.StringIO()
    with pytest.raises(CommandFailure) as error:
        adapter.run(('sudo', '-n', 'loginctl', 'enable-linger', '1000'), env={}, log=log)
    assert calls[0][1]['input'] == secret + '\n'
    assert calls[0][0][:6] == ('sudo', '-S', '-k', '-p', '', '--')
    assert secret not in str(error.value) + log.getvalue() + repr(adapter)
    with pytest.raises(PermissionError):
        adapter.run(('sudo', '-n', 'sh', '-c', 'anything'), env={}, log=log)
    assert len(calls) == 1


def test_missing_credentials_and_policy_errors_do_not_claim_success(tmp_path):
    paths, p, store = prepared(tmp_path)
    io = SystemIO(fail='enable-linger')
    r = recovery.Recovery(paths, store, io=io, uid=1000, boot_id=lambda: BOOT_A)
    setup = Installer(paths, store, io=io).create(p)
    with pytest.raises(CommandFailure):
        r.prepare(setup.setup_id, approval(p, privileged=True), service_digest=service_consent(paths, p)[1])
    with pytest.raises((FileNotFoundError, ApprovalRequired)):
        r.resume(setup.setup_id, setup.plan_digest)
    assert store.get_setup(setup.setup_id).state != SetupState.READY


def test_reconnect_pinning_backoff_deadline_and_cancellation():
    now = [0.0]
    attempts = []
    class Connection:
        def connect(self, *args, **kwargs):
            attempts.append(kwargs)
            if len(attempts) == 1:
                raise ControllerError('connection_failed')
            return {'state': 'awaiting_authentication'}
        def authenticate(self, password, **kwargs):
            assert password == 'memory-only'
        def close(self):
            pass
    cancel = threading.Event()
    def wait(delay):
        now[0] += delay
        return cancel.is_set()
    connection = recovery.reconnect(Connection, ('host', 22, 'user'), 'memory-only',
                                    timeout=8, cancel=cancel, clock=lambda: now[0], wait=wait)
    assert isinstance(connection, Connection) and len(attempts) == 2
    with pytest.raises(ControllerError, match='authentication_required'):
        recovery.reconnect(Connection, ('host', 22, 'user'), None, timeout=8, cancel=cancel)
    class Changed(Connection):
        def connect(self, *args, **kwargs):
            raise ControllerError('host_key_changed')
    with pytest.raises(ControllerError, match='host_key_changed'):
        recovery.reconnect(Changed, ('host', 22, 'user'), 'pw', timeout=8, cancel=cancel)
    cancel.set()
    with pytest.raises(ControllerError, match='cancelled'):
        recovery.reconnect(Connection, ('host', 22, 'user'), 'pw', timeout=8, cancel=cancel)


def test_tampered_service_receipt_or_plan_never_resumes(tmp_path):
    paths, p, store = prepared(tmp_path)
    io = SystemIO()
    r = recovery.Recovery(paths, store, io=io, uid=1000, boot_id=lambda: BOOT_A)
    setup = Installer(paths, store, io=io).create(p)
    r.prepare(setup.setup_id, approval(p, privileged=True), service_digest=service_consent(paths, p)[1])
    receipt = paths.bootstrap / setup.plan_digest / 'recovery.json'
    data = json.loads(receipt.read_text())
    data['service_plan']['uid'] = 0
    receipt.write_text(json.dumps(data))
    before = len(io.calls)
    with pytest.raises(ApprovalRequired):
        r.resume(setup.setup_id, setup.plan_digest)
    assert len(io.calls) == before


def test_ssh_recovery_authenticates_before_secret_and_only_fixed_composition(tmp_path):
    from test_ssh import trusted_connection
    from h3server.installer.planner import ReleaseArtifact
    network, connection = trusted_connection(tmp_path)
    artifact = ReleaseArtifact('v1', 'h3server-0.1.0-py3-none-any.whl', 'a' * 64)
    result = connection.recovery(root='/home/test/App % space', artifact=artifact,
                                 digest='b' * 64, action='prepare', service_digest='c' * 64,
                                 sudo_password='stdin-secret')
    assert result.succeeded and result.stdout == ''
    command = network.commands[-1]
    assert 'sha256sum -c -' in command and '--detach' in command
    assert 'stdin-secret' not in command
    assert json.loads(b''.join(network.sftp_packets)) == {'sudo_password': 'stdin-secret'}
    connection.close()
    before = len(network.sftp_packets)
    with pytest.raises(ControllerError, match='authentication_required'):
        connection.recovery(root='/home/test/app', artifact=artifact, digest='b' * 64,
                            action='reboot', sudo_password='stdin-secret')
    assert len(network.sftp_packets) == before
    for action in ['sh', 'reboot; id']:
        with pytest.raises(ValueError):
            connection.recovery(root='/home/test/app', artifact=artifact, digest='b' * 64, action=action)


def test_reconnect_real_trust_store_rejects_changed_host_without_auth(tmp_path):
    from test_ssh import trusted_connection
    import paramiko
    network, connection = trusted_connection(tmp_path)
    connection.close()
    count = len(network.auth_calls)
    network.key = paramiko.RSAKey.generate(1024)
    with pytest.raises(ControllerError, match='host_key_changed'):
        recovery.reconnect(lambda: network.connection(tmp_path / 'local'),
                           ('example.org', 22, 'test'), 'must-not-send', timeout=2)
    assert len(network.auth_calls) == count
    assert all(t.closed for t in network.transports)


def test_reconnect_exhausts_deadline_and_closes_failures():
    now, closed = [0.0], []
    class Offline:
        def connect(self, *args, timeout):
            now[0] += timeout
            raise ControllerError('connection_failed')
        def close(self):
            closed.append(True)
    with pytest.raises(ControllerError, match='reconnect_timeout'):
        recovery.reconnect(Offline, ('host', 22, 'user'), 'pw', timeout=2,
                           clock=lambda: now[0], wait=lambda seconds: False)
    assert now[0] == 2 and closed == [True]


def test_templates_match_packaged_composition():
    root = Path(__file__).resolve().parents[1]
    assert (root / 'templates/bootstrap.service').read_text() == recovery.BOOTSTRAP_UNIT
    assert (root / 'templates/h3server.service').read_text() == recovery.DAEMON_UNIT


def test_sudo_session_availability_not_authorization(tmp_path):
    paths, p, store = prepared(tmp_path, sudo_available=False, sudo_password_available=True,
                              packages=('git=1',), package_changes=('Inst git (1 Debian [amd64])',))
    assert p['sudo_authentication_required'] and not p['blockers']
    with pytest.raises(ApprovalRequired):
        recovery.Recovery(paths, store, uid=1000).prepare(
            Installer(paths, store).create(p).setup_id, approval(p),
            service_digest=service_consent(paths, p)[1])
    calls = []
    def denied(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1)
    adapter = recovery.PrivilegedIO(p, uid=1000, password='bad', run=denied)
    # No fixture claim that availability authorizes any package modification.
    with pytest.raises(CommandFailure):
        adapter.run(('sudo', '-n', 'true'), env={}, log=io.StringIO())
    assert len(calls) == 1


def test_receipts_private_boot_durable_before_reboot_and_ambiguous_failure_not_retried(tmp_path):
    paths, p, store = prepared(tmp_path, driver_working=False, driver_package='nvidia-driver-580',
                              packages=('nvidia-driver-580=1',), package_changes=('Inst nvidia-driver-580 (1 Ubuntu [amd64])',))
    class Disconnect(AptIO, SystemIO):
        reboot_calls = 0
        def run(self, argv, **kw):
            if argv == ('sudo', '-n', 'systemctl', 'reboot'):
                self.reboot_calls += 1
                assert store.get_setup(setup.setup_id).state == SetupState.WAITING_FOR_REBOOT
                record = json.loads((paths.bootstrap / setup.plan_digest / 'boot.json').read_text())
                assert record['reboot_requested'] and record['reboot_approval']['reboot']
                raise CommandFailure('connection lost during reboot request')
            return super().run(argv, **kw)
    io = Disconnect(simulation='Inst nvidia-driver-580 (1 Ubuntu [amd64])')
    r = recovery.Recovery(paths, store, io=io, uid=1000, boot_id=lambda: BOOT_A)
    setup = Installer(paths, store, io=io).create(p)
    r.prepare(setup.setup_id, approval(p, privileged=True), service_digest=service_consent(paths, p)[1])
    r.resume(setup.setup_id, setup.plan_digest)
    with pytest.raises(CommandFailure):
        r.request_reboot(setup.setup_id, approval(p, privileged=True, reboot=True))
    r.request_reboot(setup.setup_id, approval(p, privileged=True, reboot=True))
    assert io.reboot_calls == 1
    import stat
    for name in ['boot.json', 'recovery.json']:
        assert stat.S_IMODE((paths.bootstrap / setup.plan_digest / name).stat().st_mode) == 0o600


def test_unprivileged_resume_refuses_unfinished_package_stage(tmp_path):
    paths, p, store = prepared(tmp_path)
    runner = Installer(paths, store, io=FakeIO())
    setup = runner.create(p)
    with pytest.raises(PermissionError, match='prerequisites'):
        runner.run(setup.setup_id, approval(p), unprivileged=True)
    assert store.get_setup(setup.setup_id).state == SetupState.AWAITING_APPROVAL


def test_real_detached_launcher_closes_inherited_pipes(tmp_path):
    import os
    import sys
    import time
    if not hasattr(os, 'fork'):
        pytest.skip('Linux target detach primitive requires fork; not Windows controller code')
    marker = tmp_path / 'detached'
    code = ('from h3server.installer.recovery import _detach; from pathlib import Path; import time,os; '
            f'child=_detach();\nif child:\n time.sleep(.2)\n Path({str(marker)!r}).write_text(str(os.getsid(0)))\n')
    result = subprocess.run((sys.executable, '-c', code), capture_output=True, timeout=5,
                            env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')})
    assert result.returncode == 0 and not result.stdout and not result.stderr
    deadline = time.monotonic() + 3
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert int(marker.read_text()) != os.getsid(0)


def test_python_absent_password_prerequisites_fixed_stdin_contract(tmp_path, monkeypatch):
    from test_ssh import trusted_connection
    from h3server.probe import CommandResult
    paths, p, store = prepared(tmp_path, python_ready=False, sudo_available=False,
                              sudo_password_available=True, packages=('python3=3.12', 'python3-venv=3.12'),
                              package_changes=('Inst python3 (3.12 Ubuntu [amd64])', 'Inst python3-venv (3.12 Ubuntu [amd64])'))
    network, connection = trusted_connection(tmp_path)
    calls, files = [], {}
    installed = [False]
    class File:
        def __init__(self, name): self.name = name
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def write(self, value): files[self.name] = value
        def flush(self): pass
    class SFTP:
        def open(self, name, mode): return File(name)
        def chmod(self, *args): pass
        def posix_rename(self, source, target): files[target] = files.pop(source)
        def close(self): pass
    monkeypatch.setattr(connection, 'open_sftp', SFTP)
    def execute(command, timeout, *, stdin=b'', suppress_output=False):
        calls.append((command, stdin, suppress_output))
        if 'cat /etc/os-release' in command: return CommandResult('ID=ubuntu\nVERSION_ID="24.04"')
        if 'uname -m' in command: return CommandResult('x86_64')
        if 'dpkg-query' in command: return CommandResult('install ok installed\t3.12\n' if installed[0] else '')
        if 'apt-get -s' in command:
            return CommandResult('' if installed[0] else '\n'.join(p['package_changes']))
        if 'apt-get -y' in command: installed[0] = True
        return CommandResult('')
    monkeypatch.setattr(connection, '_execute', execute)
    result = connection.install_prerequisites(root='/home/test/app', plan=p,
                    approval=approval(p, privileged=True), sudo_password='only-stdin')
    assert result == 'waiting_for_replan' and installed[0]
    mutating = [c for c in calls if 'sudo' in c[0]]
    assert mutating and all(c[1] == b'only-stdin\n' for c in mutating)
    assert all(c[2] for c in mutating)
    assert all('only-stdin' not in c[0] for c in calls)
    assert all('only-stdin' not in str(data) for data in files.values())
    assert all('flock -n' in c[0] and 'setsid --wait' in c[0] for c in mutating)
    count = sum('apt-get -y' in c[0] for c in calls)
    connection.install_prerequisites(root='/home/test/app', plan=p,
                    approval=approval(p, privileged=True), sudo_password='only-stdin')
    assert sum('apt-get -y' in c[0] for c in calls) == count
    assert any(name.endswith('/approval.json') for name in files)
    for error in ['wrong password', 'requiretty policy denial', 'transport-error']:
        def denied(command, timeout, *, stdin=b'', suppress_output=False):
            if 'sudo' in command:
                return CommandResult('only-stdin ' + error, returncode=None, error='transport-error')
            return execute(command, timeout, stdin=stdin, suppress_output=suppress_output)
        monkeypatch.setattr(connection, '_execute', denied)
        with pytest.raises(CommandFailure) as failure:
            connection.install_prerequisites(root='/home/test/app', plan=p,
                        approval=approval(p, privileged=True), sudo_password='only-stdin')
        assert 'only-stdin' not in str(failure.value) and error not in str(failure.value)
        assert 'waiting_for_replan' not in calls[-1][0]
    before = len(calls)
    with pytest.raises(ApprovalRequired):
        connection.install_prerequisites(root='/home/test/app', plan=p,
                    approval=approval(p, privileged=False), sudo_password='only-stdin')
    assert len(calls) == before
    connection.close()


def test_connect_deadline_includes_dns_and_discards_late_socket(tmp_path):
    from h3server.controller.ssh import SSHConnection
    from test_ssh import FakeSocket
    import time
    completed = threading.Event()
    socket = FakeSocket()
    def resolving(address, timeout):
        time.sleep(.08)
        completed.set()
        return socket
    connection = SSHConnection(tmp_path / 'trust', socket_factory=resolving)
    started = time.monotonic()
    with pytest.raises(ControllerError, match='connection_failed'):
        connection.connect('example.org', 22, 'test', timeout=.02)
    assert time.monotonic() - started < .07
    assert completed.wait(.5)
    deadline = time.monotonic() + .5
    while not socket.closed and time.monotonic() < deadline:
        time.sleep(.005)
    assert socket.closed
    assert connection.status()['state'] == 'disconnected'


def test_service_plan_cli_is_read_only_and_binds_target_uid(tmp_path):
    import os
    import sys
    from h3server.installer.runner import stage_bootstrap
    paths, p, store = prepared(tmp_path)
    stage_bootstrap(paths, p, approval(p))
    before = store.path.stat().st_mtime_ns
    result = subprocess.run((sys.executable, '-m', 'h3server.installer.recovery',
                             '--root', str(paths.root), '--digest', canonical_plan_digest(p), 'service-plan'),
                            check=True, capture_output=True, text=True, timeout=5,
                            env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')})
    document = json.loads(result.stdout)
    assert document == recovery.service_plan(paths, p, uid=os.getuid())
    assert store.path.stat().st_mtime_ns == before and store.list_setups() == ()


def test_service_environment_symlink_cannot_escape_root(tmp_path):
    paths, p, store = prepared(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (paths.environments / canonical_plan_digest(p)).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='escapes'):
        recovery.render_units(paths, p)
