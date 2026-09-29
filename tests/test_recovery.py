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


# Rootless subprocess harness: real /bin/sh and pipes, simulated package/sudo
# binaries. On Linux, flock/setsid/dd are the real OS tools; macOS substitutes
# only those Linux-specific utilities. This is NOT native systemd/VM acceptance.
@pytest.fixture
def prerequisite_machine(tmp_path, monkeypatch):
    import os
    import select
    import shutil
    import sys
    import time
    from test_ssh import trusted_connection
    tools = tmp_path / 'tools'
    tools.mkdir()
    machine = tmp_path / 'machine'
    machine.mkdir()
    script = '''import os,sys,time,fcntl
from pathlib import Path
name=Path(sys.argv[0]).name; args=sys.argv[1:]; root=Path(os.environ['H3_TEST_MACHINE'])
if name=='setsid': os.execvp(args[1],args[1:])
if name=='flock':
    fd=os.open(args[3],os.O_CREAT|os.O_RDWR,0o600)
    try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError: sys.exit(75)
    os.set_inheritable(fd,True); os.execvp(args[4],args[4:])
if name=='dd':
    size=int(args[0].split('=')[1]); data=b''
    while len(data)<size:
        part=os.read(0,size-len(data))
        if not part: break
        data+=part
    written=0
    try:
        while written<len(data): written+=os.write(1,data[written:])
    except BrokenPipeError: pass
    for count,direction in [(len(data),'in'),(written,'out')]:
        sys.stderr.write(('1+0' if count==size else '0+1')+' records '+direction+'\\n')
    sys.exit(0 if len(data)==size and written==size else 1)
if name=='sudo':
    with (root/'sudo-argv').open('a') as log: log.write(repr(args)+'\\n')
    os.closerange(3,256)  # emulate sudo's closefrom boundary
    mode=(root/'auth').read_text()
    if mode=='reject': sys.exit(1)
    if mode in ['accept','wrong']:
        data=b''
        while not data.endswith(b'\\n'):
            part=os.read(0,1)
            if not part: break
            data+=part
        if mode=='wrong' or data!=b'only-stdin\\n':
            print('H3DONE '+'0'*32+' 0', flush=True)
            print(data.decode(errors='replace'), file=sys.stderr)
            sys.exit(1)
    os.execvp(args[args.index('--')+1],args[args.index('--')+1:])
if name=='cat':
    if args==['/etc/os-release']: print('ID=ubuntu\\nVERSION_ID="24.04"'); sys.exit()
    os.execv('/bin/cat',['cat',*args])
if name=='uname': print('x86_64'); sys.exit()
if name=='dpkg-query':
    if (root/'installed').exists(): print('install ok installed\\t1')
    else: sys.exit(1)
if name=='apt-get':
    if '-s' in args:
        (root/'simulated').touch()
        end=time.monotonic()+8
        while (root/'simhold').exists() and not (root/'simrelease').exists() and time.monotonic()<end: time.sleep(.01)
        print('H3DONE '+'0'*32+' 0')  # ordinary output cannot forge a per-request nonce
        if not (root/'installed').exists(): print('Inst git (1 Debian [amd64])')
    if '-y' in args:
        assert os.read(0,100)==b'', 'credential reached package operation'
        (root/'mutating').write_text(str(os.getpid()))
        end=time.monotonic()+8
        while (root/'hold').exists() and not (root/'release').exists() and time.monotonic()<end: time.sleep(.01)
        (root/'installed').write_text('yes')
        with (root/'installs').open('a') as log: log.write('installed\\n')
'''
    for name in ['setsid', 'flock', 'dd', 'sudo', 'cat', 'uname', 'dpkg', 'dpkg-query', 'apt-get']:
        if name in {'setsid', 'flock', 'dd'} and sys.platform == 'linux' and shutil.which(name):
            continue
        file = tools / name
        file.write_text('#!' + sys.executable + '\n' + script)
        file.chmod(0o700)
    (machine / 'auth').write_text('accept')
    processes, connections = [], []
    class Channel:
        def __init__(self): self.eof = set()
        def settimeout(self, timeout): pass
        def exec_command(self, command):
            self.process = subprocess.Popen(('/bin/sh', '-c', command), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                env={**os.environ, 'PATH': str(tools) + os.pathsep + os.environ['PATH'], 'H3_TEST_MACHINE': str(machine)})
            processes.append(self.process)
        def send(self, data):
            return os.write(self.process.stdin.fileno(), data)
        def shutdown_write(self):
            if not self.process.stdin.closed: self.process.stdin.close()
        def close(self): self.shutdown_write()
        def ready(self, stream):
            return stream not in self.eof and bool(select.select([stream], [], [], 0)[0])
        def receive(self, stream, size):
            value = os.read(stream.fileno(), size)
            if not value: self.eof.add(stream)
            return value
        def recv_ready(self): return self.ready(self.process.stdout)
        def recv_stderr_ready(self): return self.ready(self.process.stderr)
        def recv(self, size): return self.receive(self.process.stdout, size)
        def recv_stderr(self, size): return self.receive(self.process.stderr, size)
        def exit_status_ready(self): return self.process.poll() is not None
        def recv_exit_status(self): return self.process.wait()
    def connect():
        _, connection = trusted_connection(tmp_path / ('client' + str(len(connections))))
        monkeypatch.setattr(connection._transport, 'open_session', lambda **kw: Channel())
        connections.append(connection)
        return connection
    try:
        yield machine, connect, Channel, processes
    finally:
        (machine / 'release').touch()
        (machine / 'simrelease').touch()
        for process in processes:
            if not process.stdin.closed: process.stdin.close()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
            process.stdout.close(); process.stderr.close()
        for connection in connections: connection.close()


def test_python_absent_password_prerequisites_fixed_stdin_contract(tmp_path, prerequisite_machine):
    machine, connect, _, _ = prerequisite_machine
    paths, p, _ = prepared(tmp_path / 'target', python_ready=False, sudo_available=False,
                           sudo_password_available=True, packages=('git=1',),
                           package_changes=('Inst git (1 Debian [amd64])',))
    connection = connect()
    for _ in range(2):
        assert connection.install_prerequisites(root=str(paths.root), plan=p,
                    approval=approval(p, privileged=True), sudo_password='only-stdin') == 'waiting_for_replan'
    assert (machine / 'installs').read_text() == 'installed\n'
    stage = paths.bootstrap / canonical_plan_digest(p)
    assert (stage / 'status').read_text().strip() == 'waiting_for_replan'
    assert json.loads((stage / 'approval.json').read_text())['privileged'] is True
    for file in [*stage.iterdir(), machine / 'sudo-argv']:
        assert b'only-stdin' not in file.read_bytes()
    for mode in ['nopasswd', 'wrong', 'reject']:
        (machine / 'auth').write_text(mode)
        if mode == 'nopasswd':
            (machine / 'installed').unlink()
            # Max UTF-8 frame, with no auth prompt: the real approved-operation
            # stand-in asserts that its stdin is empty, never this credential.
            assert connection.install_prerequisites(root=str(paths.root), plan=p,
                        approval=approval(p, privileged=True), sudo_password='😀' * 4096) == 'waiting_for_replan'
            for file in [*stage.iterdir(), machine / 'sudo-argv']:
                assert '😀'.encode() not in file.read_bytes()
        else:
            with pytest.raises(CommandFailure):
                connection.install_prerequisites(root=str(paths.root), plan=p,
                        approval=approval(p, privileged=True), sudo_password='only-stdin')
    with pytest.raises(ApprovalRequired):
        connection.install_prerequisites(root=str(paths.root), plan=p,
                    approval=approval(p), sudo_password='only-stdin')

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


@pytest.mark.parametrize('name', ['ordinary', 'space % dollar$ quote" slash\\segment'])
def test_systemd_scalar_paths_are_not_exec_arguments(tmp_path, name):
    paths, p, _ = prepared(tmp_path / name)
    unit = recovery.render_units(paths, p)['h3server.service']
    values = dict(line.split('=', 1) for line in unit.splitlines() if '=' in line)
    assert values['WorkingDirectory'] == str(paths.root).replace('%', '%%')
    readiness = paths.environments / canonical_plan_digest(p) / 'readiness.json'
    assert values['ConditionPathExists'] == str(readiness).replace('%', '%%')
    assert values['ExecStart'].startswith('"')


def test_native_systemd_parser_without_activation(tmp_path):
    import shutil
    import sys
    if sys.platform != 'linux' or not shutil.which('systemd-analyze'):
        pytest.skip('native systemd parser unavailable; this is not Linux unit validation')
    paths, p, _ = prepared(tmp_path / 'space % dollar$ quote"')
    directory = paths.environments / canonical_plan_digest(p)
    (directory / 'bin').mkdir(parents=True)
    (directory / 'bin/python').symlink_to(sys.executable)
    (directory / 'readiness.json').write_text('{}')
    units = []
    for name, text in recovery.render_units(paths, p).items():
        unit = tmp_path / name
        unit.write_text(text)
        units.append(str(unit))
    result = subprocess.run(('systemd-analyze', 'verify', '--man=no', *units),
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'not absolute' not in result.stderr and 'ignoring' not in result.stderr.lower()


def test_prerequisites_reject_second_connection_before_any_checkpoint_or_check(tmp_path, monkeypatch):
    """Pause A after simulation; B must not stage or simulate before A mutates."""
    from test_ssh import trusted_connection
    from contextlib import contextmanager
    from h3server.probe import CommandResult
    from h3server.installer.runner import InstallerBusy
    paths, p, _ = prepared(tmp_path, python_ready=False, packages=('git=1',),
                           package_changes=('Inst git (1 Debian [amd64])',))
    _, first = trusted_connection(tmp_path / 'first')
    _, second = trusted_connection(tmp_path / 'second')
    gate = threading.Lock()
    simulated, release = threading.Event(), threading.Event()
    calls, errors = [], []
    class Transaction:
        def run(self, argv, *, capture=False):
            calls.append(argv)
            if argv == ('cat', '/etc/os-release'): return 'ID=ubuntu\nVERSION_ID="24.04"'
            if argv == ('uname', '-m'): return 'x86_64'
            if argv[0] == 'dpkg-query': return 'install ok installed\t1\n' if ('install',) in calls else ''
            if argv[:2] == ('apt-get', '-s'):
                simulated.set()
                assert release.wait(3)
                return 'Inst git (1 Debian [amd64])'
            if 'apt-get' in argv and '-y' in argv: calls.append(('install',))
            return ''
        def finish(self): calls.append(('finish',))
    @contextmanager
    def transaction(*args, **kwargs):
        if not gate.acquire(blocking=False): raise InstallerBusy('prerequisite transaction busy')
        try:
            calls.append(('stage-approved-inputs',))
            yield Transaction()
        finally:
            gate.release()
    # Old per-command implementation ignores the transaction boundary and will
    # produce no valid platform result, demonstrating the missing exclusion.
    for connection in (first, second):
        monkeypatch.setattr(connection, '_prerequisite_transaction', transaction, raising=False)
        monkeypatch.setattr(connection, '_execute', lambda *a, **k: CommandResult(''))
        monkeypatch.setattr(connection, 'open_sftp', lambda: (_ for _ in ()).throw(RuntimeError('unlocked staging')))
    def run_first():
        try: first.install_prerequisites(root='/home/test/app', plan=p, approval=approval(p, privileged=True))
        except Exception as e: errors.append(e)
    thread = threading.Thread(target=run_first)
    thread.start()
    try:
        assert simulated.wait(2), errors
        before = list(calls)
        with pytest.raises(InstallerBusy):
            second.install_prerequisites(root='/home/test/app', plan=p, approval=approval(p, privileged=True))
        assert calls == before
    finally:
        release.set()
        thread.join(4)
        first.close()
        second.close()
    assert not errors
    assert calls.count(('install',)) == calls.count(('stage-approved-inputs',)) == 1


def _wait_for_file(path):
    import time
    deadline = time.monotonic() + 5
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert path.exists(), path


def test_real_guardian_two_connections_exclude_simulation_to_mutation_gap(tmp_path, prerequisite_machine):
    from h3server.installer.runner import InstallerBusy
    machine, connect, _, _ = prerequisite_machine
    paths, p, _ = prepared(tmp_path / 'target', python_ready=False, packages=('git=1',),
                           package_changes=('Inst git (1 Debian [amd64])',))
    first, second = connect(), connect()
    (machine / 'simhold').touch()
    errors = []
    def install():
        try:
            first.install_prerequisites(root=str(paths.root), plan=p, approval=approval(p, privileged=True), sudo_password='only-stdin')
        except Exception as e: errors.append(e)
    thread = threading.Thread(target=install)
    thread.start()
    try:
        _wait_for_file(machine / 'simulated')
        stage = paths.bootstrap / canonical_plan_digest(p)
        before = {file.name: (file.read_bytes(), file.stat().st_mtime_ns) for file in stage.iterdir()}
        with pytest.raises(InstallerBusy):
            second.install_prerequisites(root=str(paths.root), plan=p, approval=approval(p, privileged=True), sudo_password='only-stdin')
        assert {file.name: (file.read_bytes(), file.stat().st_mtime_ns) for file in stage.iterdir()} == before
        assert not (machine / 'installed').exists()
    finally:
        (machine / 'simrelease').touch()
        thread.join(8)
    assert not thread.is_alive() and not errors
    assert (machine / 'installs').read_text() == 'installed\n'


@pytest.mark.parametrize('interrupt', ['disconnect', 'guardian-crash'])
def test_lock_survives_interruption_until_privileged_child_exits(tmp_path, prerequisite_machine, interrupt):
    import os
    import signal
    from h3server.installer.runner import InstallerBusy
    machine, connect, _, processes = prerequisite_machine
    paths, p, _ = prepared(tmp_path / 'target', python_ready=False, packages=('git=1',),
                           package_changes=('Inst git (1 Debian [amd64])',))
    first, second = connect(), connect()
    (machine / 'hold').touch()
    with first._prerequisite_transaction(str(paths.root), p, approval(p, privileged=True), 'only-stdin') as transaction:
        credential = b'only-stdin\n'
        transaction.channel.send(f'install 1 {len(credential)} '.encode() + b'a' * 32 + b'\ngit=1\n' + credential)
        _wait_for_file(machine / 'mutating')
        if interrupt == 'guardian-crash':
            def parent(pid):
                return int(subprocess.check_output(('ps', '-o', 'ppid=', '-p', str(pid)), text=True).strip())
            operation = int((machine / 'mutating').read_text())
            guardian = parent(parent(operation))
            assert guardian != os.getpid()
            os.kill(guardian, signal.SIGKILL)
        else:
            transaction.channel.shutdown_write()  # cancellation/transport EOF
        with pytest.raises(InstallerBusy):
            second.install_prerequisites(root=str(paths.root), plan=p, approval=approval(p, privileged=True), sudo_password='only-stdin')
        assert not (machine / 'installed').exists()
        (machine / 'release').touch()
        _wait_for_file(machine / 'installed')
        # The external guardian/lock supervisor may still be reaping; wait for
        # EOF rather than interpreting the package marker as transaction success.
        processes[0].wait(timeout=5)
    # After SIGKILL, waitpid observes the guardian, not its orphaned supervisor.
    # Wait for the actual inherited lock lifetime rather than a timing sleep.
    import fcntl
    import time
    with (paths.state / 'installer.lock').open('a') as lock:
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                assert time.monotonic() < deadline
                time.sleep(.01)
    stage = paths.bootstrap / canonical_plan_digest(p)
    assert (stage / 'status').read_text().strip() != 'waiting_for_replan'
    # A separate authenticated transaction rechecks package state, never repeats
    # a surviving install. The guardian-crash checkpoint may remain 'running'.
    assert second.install_prerequisites(root=str(paths.root), plan=p, approval=approval(p, privileged=True), sudo_password='only-stdin') == 'waiting_for_replan'
    assert (machine / 'installs').read_text() == 'installed\n'


def test_truncated_credential_frame_cannot_mutate_or_poison_parser(tmp_path, prerequisite_machine):
    machine, connect, _, processes = prerequisite_machine
    paths, p, _ = prepared(tmp_path / 'target', python_ready=False, packages=('git=1',),
                           package_changes=('Inst git (1 Debian [amd64])',))
    (machine / 'auth').write_text('nopasswd')
    connection = connect()
    nonce = 'a' * 32
    with connection._prerequisite_transaction(str(paths.root), p, approval(p, privileged=True), 'only-stdin') as transaction:
        transaction.channel.send(f'install 1 11 {nonce}\ngit=1\nonly'.encode())
        transaction.channel.shutdown_write()
        with pytest.raises(CommandFailure):
            transaction.receive(nonce)
        processes[-1].wait(timeout=5)
    assert not (machine / 'installed').exists()
    stage = paths.bootstrap / canonical_plan_digest(p)
    assert (stage / 'status').read_text().strip() == 'failed'
    assert b'only' not in (stage / 'frame-stat').read_bytes()
    assert connection.install_prerequisites(root=str(paths.root), plan=p, approval=approval(p, privileged=True), sudo_password='only-stdin') == 'waiting_for_replan'


def test_prerequisite_protocol_has_no_arbitrary_command_or_argument_escape(tmp_path):
    paths, p, _ = prepared(tmp_path, python_ready=False, packages=('git=1',),
                           package_changes=('Inst git (1 Debian [amd64])',))
    protocol = recovery.PrerequisiteProtocol(str(paths.root), p, approval(p, privileged=True))
    for argv in [('sudo', '-n', 'sh'), ('sudo', '-n', 'env', 'DEBIAN_FRONTEND=noninteractive', 'dpkg', '--configure', 'unapproved'),
                 ('cat', '/etc/shadow'), ('sh', '-c', 'id')]:
        with pytest.raises(PermissionError): protocol.request(argv, 'secret')
    assert 'eval ' not in protocol.command('a' * 32)


@pytest.mark.parametrize('frame', [
    b'authorize 0 99999 ' + b'a' * 32 + b'\n',
    b'shell 0 0 ' + b'a' * 32 + b'\n',
    b'install 1 0 ' + b'a' * 32 + b'\ngit=1;not-a-command\n',
])
def test_guardian_rejects_invalid_frames_before_sudo(tmp_path, prerequisite_machine, frame):
    machine, connect, _, _ = prerequisite_machine
    paths, p, _ = prepared(tmp_path / 'target', python_ready=False, packages=('git=1',),
                           package_changes=('Inst git (1 Debian [amd64])',))
    connection = connect()
    with connection._prerequisite_transaction(str(paths.root), p, approval(p, privileged=True), None) as transaction:
        transaction.channel.send(frame)
        with pytest.raises(CommandFailure): transaction.receive('a' * 32)
    assert not (machine / 'sudo-argv').exists()
    assert not (machine / 'installed').exists()


def test_privileged_io_nopasswd_cannot_pass_credentials_to_os_command(tmp_path, prerequisite_machine):
    import os
    machine, _, _, _ = prerequisite_machine
    (machine / 'auth').write_text('nopasswd')
    paths, p, _ = prepared(tmp_path / 'target', packages=('git=1',),
                           package_changes=('Inst git (1 Debian [amd64])',))
    adapter = recovery.PrivilegedIO(p, uid=1000, password='only-stdin')
    env = {**os.environ, 'PATH': str(machine.parent / 'tools') + os.pathsep + os.environ['PATH'],
           'H3_TEST_MACHINE': str(machine)}
    adapter.run(('sudo', '-n', 'env', 'DEBIAN_FRONTEND=noninteractive', 'apt-get',
                 '-y', '--no-remove', 'install', 'git=1'), env=env, log=io.StringIO())
    assert (machine / 'installed').exists()  # fake operation asserts empty stdin
    assert 'only-stdin' not in (machine / 'sudo-argv').read_text()


def test_privileged_io_timeout_never_exposes_password(tmp_path):
    _, p, _ = prepared(tmp_path)
    calls = []
    def timeout(argv, **kwargs):
        calls.append((argv, kwargs))
        raise subprocess.TimeoutExpired(argv, 3600, output='only-stdin', stderr='only-stdin')
    log = io.StringIO()
    with pytest.raises(CommandFailure) as failure:
        recovery.PrivilegedIO(p, uid=1000, password='only-stdin', run=timeout).run(
            ('sudo', '-n', 'true'), env={}, log=log)
    assert len(calls) == 1 and calls[0][1]['timeout'] == 3600
    assert 'only-stdin' not in str(failure.value) + log.getvalue() + repr(calls[0][0])
