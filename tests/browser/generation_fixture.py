"""TEST ONLY: real controller + daemon/store/worker with mocked SSH/runtime IO.

Never connects SSH, installs packages, downloads weights, or claims H3 output.
The fixture 'video' bytes are not a playable/generated MP4.
"""
from dataclasses import asdict, replace
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

import uvicorn
from fastapi.responses import HTMLResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from controller_fixture import BrowserNetwork, BrowserTransport
from test_deploy import LocalSFTP, bundle
from test_install_plan import facts
from test_installer import FakeIO, AptIO
from test_recovery import SystemIO, BOOT_A, BOOT_B
from test_worker import fixture_backend
from h3server.controller.app import create_app
from h3server.controller.ssh import SSHConnection, ControllerError
from h3server.installer.recovery import service_plan, Recovery
from h3server.installer.planner import Approval
from h3server.installer.runner import Installer
from h3server.paths import AppPaths
from h3server.probe import CommandResult
from h3server.remote.app import create_app as remote_app, load_token
from h3server.remote.worker import Worker
from h3server.store import Store, SetupState, canonical_plan_digest


class ControlledIO(FakeIO):
    """Hold a real running checkpoint until the browser test releases IO."""
    def run(self, *args, **kwargs):
        assert self.finish.wait(60), 'TEST ONLY installer IO gate timed out'
        return super().run(*args, **kwargs)


class PackageIO(SystemIO):
    """Real child output/exit diagnostics; never executes pip or native setup."""
    def __init__(self, connection, paths):
        super().__init__()
        self.connection, self.paths = connection, paths
        self.success = False

    def run(self, argv, **kw):
        if argv[:2] == ('loginctl', 'show-user'):
            return 'yes\n'
        if 'pip' in argv and 'install' in argv and 'help' not in argv:
            from h3server.installer.steps import ProcessIO
            self.connection.calls.append('pip-attempt')
            if os.environ.get('H3_TEST_CONTROLLED_SETUP') == 'pip-fresh-success' and self.connection.calls.count('pip-attempt') > 1:
                self.success = True
            if '--no-cache-dir' in argv:
                self.connection.calls.append('pip-fresh-index')
            message = ('Installed av==18.1.0' if self.success else
                'ResolutionImpossible: conflicting dependencies for av==18.1.0' if os.environ.get('H3_TEST_CONTROLLED_SETUP') == 'pip-permanent'
                else 'No matching distribution found for av==18.1.0')
            script = ("from pathlib import Path; import time; "
                      "print('Collecting av==18.1.0', flush=True); "
                      "print('Authorization: Bearer TEST-ONLY-secret', flush=True)\n"
                      "print('HTTPS://alice:URLLEAK@example.test/wheel?sig=QUERYLEAK', flush=True)\n"
                      "print('hTtPs://alice:MIXEDLEAK@example.test/wheel#FRAGMENTLEAK', flush=True)\n"
                      "while not Path(" + repr(str(self.paths.root/'fixture-package-finish')) + ").exists(): time.sleep(.02)\n"
                      "print(" + repr(message) + ", flush=True)\n"
                      "raise SystemExit(" + str(0 if self.success else 1) + ")")
            return ProcessIO(command_timeout=30).run((sys.executable, '-u', '-c', script), **kw)
        return super().run(argv, **kw)


class DirectTransport(BrowserTransport):
    def open_channel(self, kind, destination, source, timeout):
        assert kind == 'direct-tcpip' and destination == ('127.0.0.1', 8765)
        channel = socket.create_connection(('127.0.0.1', self.network.api_port), timeout)
        self.channels.append(channel)
        return channel


class Network(BrowserNetwork):
    def socket(self, address, timeout):
        if time.monotonic() < getattr(self, 'unavailable_until', 0):
            raise OSError('TEST ONLY delayed transport restoration')
        result = super().socket(address, timeout)
        self.outputs["printf '%s' \"$HOME\""] = str(self.home).encode()
        return result
    def transport(self, sock):
        result = DirectTransport(self, sock); self.transports.append(result); return result


class FixtureSSH(SSHConnection):
    """Mock only target SSH commands/SFTP; compose real plans/installer/store."""
    def _staging_transaction(self, root, *, discovery=False):
        return LocalSFTP("/").staging_transaction(root, discovery=discovery)
    def open_sftp(self):
        self._require_authentication()
        return LocalSFTP('/')
    def check_setup_password(self, password):
        self._require_authentication()
        assert password == 'TEST-ONLY-secret'
        self.calls.append('sudo-check')
        if os.environ.get('H3_TEST_CONTROLLED_SETUP') == 'sudo-rejected':
            raise ControllerError('setup_sudo_password_rejected')

    def install_prerequisites(self, *, root, plan, approval, sudo_password, **kwargs):
        assert sudo_password == 'TEST-ONLY-secret'
        assert approval.plan_digest == canonical_plan_digest(plan)
        self.calls.append('prerequisites')
        paths = AppPaths(root)
        # Fixed privileged bootstrap boundary only; persist its real protocol.
        status = paths.bootstrap / approval.plan_digest / 'status'
        status.write_text('waiting_for_replan\n'); status.chmod(0o600)
        self.prerequisites_ready = True

    def inspect_install(self, **kwargs):
        self._require_authentication()
        if os.environ.get('H3_TEST_CONTROLLED_SETUP') == 'replan' and not getattr(self, 'prerequisites_ready', False):
            return facts(python_ready=False, packages=('python3-venv=1',),
                         package_changes=('Inst python3-venv (1 Ubuntu [amd64])',))
        if os.environ.get('H3_TEST_CONTROLLED_SETUP') == 'reboot':
            return facts(driver_working=False, driver_package='nvidia-driver-580',
                packages=('nvidia-driver-580=1',), package_changes=('Inst nvidia-driver-580 (1 Ubuntu [amd64])',))
        return facts()
    def recovery(self, *, root, artifact, digest, action, **kwargs):
        self._require_authentication()
        self.calls.append(action)
        if action in {'prepare', 'reboot'}: assert kwargs['sudo_password'] == 'TEST-ONLY-secret'
        paths = AppPaths(root)
        plan = json.loads((paths.bootstrap/digest/'plan.json').read_text())
        if action == 'service-plan':
            uid = os.getuid() if os.environ.get('H3_TEST_CONTROLLED_SETUP', '').startswith('pip-') else 1000
            return CommandResult(json.dumps(service_plan(paths, plan, uid=uid)), returncode=0)
        store = Store(paths.state/'setup.sqlite3')
        runner = Installer(paths, store, io=FakeIO())
        if os.environ.get('H3_TEST_CONTROLLED_SETUP') == 'reboot':
            class RebootIO(AptIO, SystemIO):
                pass
            if not hasattr(self, 'reboot_io'):
                self.reboot_io = RebootIO(simulation='Inst nvidia-driver-580 (1 Ubuntu [amd64])')
                self.boot = BOOT_A
            recovery = Recovery(paths, store, io=self.reboot_io, uid=1000, boot_id=lambda: self.boot)
            approval = Approval(**json.loads((paths.bootstrap/digest/'approval.json').read_text()))
            if action == 'prepare':
                setup = runner.create(plan)
                recovery.prepare(setup.setup_id, approval, service_digest=kwargs['service_digest'])
                recovery.resume(setup.setup_id, digest)
            elif action == 'reboot':
                setup = store.list_setups()[0]
                recovery.request_reboot(setup.setup_id, approval)  # mocked system IO only
                self._transport.network.unavailable_until = time.monotonic() + 4
                self.close()
                def boot_continuation():
                    self.boot = BOOT_B
                    self.reboot_io.driver = True
                    recovery.resume(setup.setup_id, digest)  # independent remote bootstrap fixture
                timer = threading.Timer(4, boot_continuation); timer.daemon = True; timer.start()
            else:
                raise AssertionError('TEST ONLY unsupported reboot fixture action')
            return CommandResult(returncode=0)
        if action == 'prepare' and os.environ.get('H3_TEST_CONTROLLED_SETUP', '').startswith('pip-'):
            self.prepare_package_failure(paths, plan, wait=False)
            return CommandResult(returncode=0)
        if action == 'prepare':
            approval = Approval(**json.loads((paths.bootstrap/digest/'approval.json').read_text()))
            assert kwargs['service_digest'] == canonical_plan_digest(service_plan(paths, plan, uid=1000))
            if os.environ.get('H3_TEST_CONTROLLED_SETUP') in {'planned', 'awaiting_approval'}:
                setup = store.create_setup(plan)
                if os.environ['H3_TEST_CONTROLLED_SETUP'] == 'awaiting_approval':
                    store.transition_setup(setup.setup_id, SetupState.AWAITING_APPROVAL)
                io = ControlledIO(); io.finish = self.finish_setup
                runner = Installer(paths, store, io=io)
                def launch():
                    if self.start_setup.wait(60):
                        with patch('h3server.backends.comfy_runtime.verify_sources'):
                            runner.run(setup.setup_id, approval)
                threading.Thread(target=launch, daemon=True).start()
            else:
                setup = runner.create(plan)
                # TEST ONLY source/runtime IO: no checkout, pip, CUDA or weights.
                with patch('h3server.backends.comfy_runtime.verify_sources'):
                    runner.run(setup.setup_id, approval)
            if os.environ.get('H3_TEST_CONTROLLED_SETUP') == 'stale-release':
                (paths.config/'remote-token').write_text(self.fixture_token)
                (paths.config/'remote-token').chmod(0o600)
            return CommandResult(returncode=0)
        raise AssertionError('TEST ONLY unsupported SSH action')
    def prepare_package_failure(self, paths, plan, *, wait):
        from h3server.installer.steps import CommandFailure
        from test_install_plan import approval
        store = Store(paths.state/'setup.sqlite3')
        self.package_io = PackageIO(self, paths)
        self.package_recovery = Recovery(paths, store, io=self.package_io, uid=os.getuid())
        self.package_setup = Installer(paths, store, io=self.package_io).create(plan)
        self.package_recovery.prepare(self.package_setup.setup_id, approval(plan, privileged=True),
            service_digest=canonical_plan_digest(service_plan(paths, plan, uid=os.getuid())))
        def run():
            try:
                with patch('h3server.backends.comfy_runtime.verify_sources'):
                    self.package_recovery.resume(self.package_setup.setup_id, self.package_setup.plan_digest)
            except CommandFailure:
                pass  # Actual durable failed checkpoint/diagnostics, not a fake HTTP response.
        if wait: run()
        else: threading.Thread(target=run, daemon=True).start()

    def deployment_action(self, *, root, artifact, digest, action, data):
        self._require_authentication()
        from h3server.controller.target import snapshot
        paths = AppPaths(root)
        if action == 'snapshot': return snapshot(paths, Store(paths.state/'setup.sqlite3'), data['after'], digest)
        if action in {'retry-stage', 'activate', 'download', 'cancel-download'}:
            self.calls.append(action)
            if action == 'activate': assert data['policy'] == 'reject' and not data['cancel_confirmed']
            command = [sys.executable, str(Path(__file__).with_name('target_fixture.py')),
                       '--root', root, '--digest', digest, action]
            prior = (paths.state/'operation.json').read_text() if (paths.state/'operation.json').exists() else None
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True)
            process.stdin.write(json.dumps(data)); process.stdin.close()
            if action == 'cancel-download':
                process.wait(10)
                output = process.stdout.read(); error = process.stderr.read()
                process.stdout.close(); process.stderr.close()
                if process.returncode: raise ControllerError('deployment_operation_failed', 502)
                return json.loads(output)
            # The real target owns the operation lock and durable records; the
            # fixture launches a local subprocess instead of forking over SSH.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    text = (paths.state/'operation.json').read_text()
                    record = json.loads(text)
                    if text != prior and record['action'] == action:
                        def reap():
                            process.wait(60)
                            process.stdout.close(); process.stderr.close()
                        threading.Thread(target=reap, daemon=True).start()
                        return {'state': 'accepted', 'operation': record['operation']}
                except FileNotFoundError:
                    pass
                if process.poll() is not None:
                    process.stdout.close(); process.stderr.close()
                    raise ControllerError('deployment_operation_failed', 502)
                time.sleep(.01)
            process.kill(); process.wait(); process.stdout.close(); process.stderr.close()
            raise AssertionError('TEST ONLY target launch timeout')
        raise AssertionError('TEST ONLY unsupported deployment action')


def main():
    with tempfile.TemporaryDirectory(prefix='h3-TEST-ONLY-') as temporary, socket.socket() as remote_listener, socket.socket() as listener:
        home = Path(temporary).resolve()/'home'; home.mkdir()
        paths = AppPaths(home/'MinimaxH3Server')
        for directory in paths.directories(): directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        backend = fixture_backend(Path(temporary), '''import json,pathlib,sys,time
request=json.loads(pathlib.Path(sys.argv[2]).read_text())
if 'cancel' in request['request']['prompt']: time.sleep(30)
print(json.dumps({'stage':'denoising','status':'running','message':'TEST ONLY','progress':{'completed':1,'total':2,'unit':'steps'}}),flush=True)
time.sleep(.1)
pathlib.Path(request['output_path']).write_bytes(b'TEST ONLY fixture-mp4 bytes, not real generated video')
''')
        # The fixture validates its explicit marker, not an actual MP4 codec.
        from h3server.backends.h3 import CAPABILITIES
        backend = replace(backend, capabilities=CAPABILITIES,
            validate_video=lambda fd: __import__('os').read(fd, 100).startswith(b'TEST ONLY fixture-mp4'))
        profile = os.environ.get('H3_TEST_PRUNED_PROFILE')
        if profile:
            from h3server.backends.pruned import CAPABILITIES as PRUNED_CAPABILITIES
            from h3server.backends.catalog import get_artifact_set
            selected = get_artifact_set(profile)
            backend = replace(backend, capabilities=PRUNED_CAPABILITIES,
                              runtime_id=selected.runtime_id, profiles=(profile,))
        # A stale *discovery* target cannot already contain the fixture daemon's
        # live-work evidence. Isolate that daemon in this regression mode only.
        daemon_paths = AppPaths(Path(temporary)/'fixture-daemon') if os.environ.get('H3_TEST_CONTROLLED_SETUP') == 'stale-release' else paths
        for directory in daemon_paths.directories(): directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        store = Store(daemon_paths.state/'remote.sqlite')
        if os.environ.get('H3_TEST_MEMORY_SETTINGS') == '1':
            # TEST ONLY policy API seam. These tests never submit GPU/fixture jobs.
            backend = replace(backend, resident=True)
        worker = Worker(store, daemon_paths, backend=backend, min_free_bytes=0)
        daemon = remote_app(store, worker, token=load_token(daemon_paths), heartbeat_seconds=.2)
        remote_listener.bind(('127.0.0.1',0))
        server = uvicorn.Server(uvicorn.Config(daemon, log_level='critical', access_log=False))
        thread = threading.Thread(target=lambda: server.run(sockets=[remote_listener]), daemon=True)
        thread.start()
        while not server.started: time.sleep(.01)
        network = Network(); network.home = home; network.api_port = remote_listener.getsockname()[1]
        network.outputs.update({'LC_ALL=C uname -s':b'Linux', 'LC_ALL=C uname -m':b'x86_64',
            'LC_ALL=C cat /etc/os-release':b'ID=ubuntu\nVERSION_ID="24.04"\n',
            'LC_ALL=C cat /proc/meminfo': b'MemTotal: 30408704 kB\n',
            'LC_ALL=C nvidia-smi --query-gpu=pci.bus_id,name,memory.total,driver_version --format=csv,noheader,nounits':
                b'00000000:01:00.0, NVIDIA GeForce RTX 4090, 24564, 580.1\n'})
        connection = FixtureSSH(Path(temporary)/'trust', socket_factory=network.socket, transport_factory=network.transport)
        connection.fixture_token = load_token(daemon_paths)
        connection.start_setup = threading.Event(); connection.finish_setup = threading.Event(); connection.calls = []
        # Private subprocess pipe only: no fixture controls on the HTTP API.
        def controls():
            for line in sys.stdin:
                command = json.loads(line)
                if command['action'] == 'calls':
                    print(json.dumps({'calls': connection.calls}), flush=True); continue
                elif command['action'] == 'stage-current-plan':
                    document = app.state.deployment.make_plan('h3-convrot-int8', sudo_password_available=True)
                    connection.calls.clear()
                    print(json.dumps({'plan_digest': document['plan_digest']}), flush=True)
                    continue
                elif command['action'] == 'stage-stale-release':
                    from h3server.controller.deploy import Deployment, ReleaseBundle, _write
                    from h3server.installer.planner import build_plan
                    from test_deploy import wheel
                    old_dir = Path(temporary)/'old-release'; old_dir.mkdir()
                    if command.get('trusted'):
                        from h3server.installer.compatibility import PREVIOUS_ARTIFACT, previous_payload
                        old_wheel = old_dir/PREVIOUS_ARTIFACT.filename
                        old_wheel.write_bytes(previous_payload())
                    else:
                        old_wheel = wheel(old_dir, 'h3server/old_only.py')
                    old = ReleaseBundle(old_wheel, hashlib.sha256(old_wheel.read_bytes()).hexdigest())
                    assert old.artifact != app.state.deployment.release.artifact
                    prior = Deployment(connection, old)
                    prior.make_plan('h3-convrot-int8', sudo_password_available=True)
                    # Match the target's completed prerequisite history without
                    # performing packages or changing its unlaunched env pointer.
                    history = build_plan(facts(python_ready=False, packages=('python3-venv=1',),
                        package_changes=('Inst python3-venv (1 Ubuntu [amd64])',)),
                        profile='h3-convrot-int8', artifact=old.artifact)
                    history['deployment_config'] = {'port': 8765, 'root': prior.root}
                    digest = canonical_plan_digest(history)
                    with connection.private_sftp() as sftp:
                        stage = f'{prior.root}/bootstrap/{digest}'
                        _write(sftp, stage + '/plan.json', json.dumps(history, sort_keys=True).encode())
                        _write(sftp, stage + '/approval.json', json.dumps(asdict(Approval(digest, True, True, True, False))).encode())
                        _write(sftp, stage + '/status', b'waiting_for_replan\n')
                    Store(paths.state/'setup.sqlite3')
                    (paths.state/'setup.sqlite3').chmod(0o600)
                    connection.calls.clear()
                    print(json.dumps({'old_release':old.artifact.sha256,
                        'current_release':app.state.deployment.release.artifact.sha256,
                        'staged_history_count':len(list(paths.bootstrap.iterdir()))}), flush=True)
                    continue
                elif command['action'] == 'stage-trusted-failure':
                    from h3server.controller.deploy import Deployment, ReleaseBundle
                    from h3server.installer.compatibility import PREVIOUS_ARTIFACT, PREVIOUS_SHA256, previous_payload
                    old_dir = Path(temporary)/'trusted-predecessor'; old_dir.mkdir()
                    wheel = old_dir/PREVIOUS_ARTIFACT.filename; wheel.write_bytes(previous_payload())
                    prior = Deployment(connection, ReleaseBundle(wheel, PREVIOUS_SHA256))
                    prior.make_plan('h3-convrot-int8')
                    from test_install_plan import approval
                    prior._stage(approval(prior.plan, privileged=True))
                    (paths.root/'fixture-package-finish').touch()
                    connection.prepare_package_failure(paths, prior.plan, wait=True)
                    # Previous release persisted a plain installer log, not the
                    # new diagnostic schema. Retain actual child output as legacy.
                    diagnostic = paths.logs/f'installer-{connection.package_setup.setup_id}.diagnostics.json'
                    legacy = paths.logs/f'installer-{connection.package_setup.setup_id}.log'
                    legacy.write_text(json.loads(diagnostic.read_text())['tail']); legacy.chmod(0o600)
                    diagnostic.unlink()
                    # Simulate loss of controller memory, preserving real target state.
                    app.state.setup.reset(); app.state.deployment.reset()
                    connection.calls.clear()
                    print(json.dumps({'plan_digest':canonical_plan_digest(prior.plan)}), flush=True)
                    continue
                elif command['action'] == 'invalidate-service-receipt':
                    (paths.config/'systemd/h3-bootstrap.service').write_text('TEST ONLY changed service unit')
                elif command['action'] == 'finish-package': (paths.root/'fixture-package-finish').touch()
                elif command['action'] == 'allow-package-success':
                    connection.package_io.success = True
                    (paths.root/'fixture-package-success').touch()
                    if command.get('gated'):
                        (paths.root/'fixture-retry-gated').touch()
                elif command['action'] == 'retry-checkpoint':
                    print(json.dumps({'ready': (paths.root/'fixture-retry-ready').exists(),
                        'recorded': (paths.root/'fixture-retry-recorded').exists()}), flush=True)
                    continue
                elif command['action'] == 'finish-retry-work':
                    if command.get('interrupt'):
                        (paths.root/'fixture-retry-interrupt').touch()
                    (paths.root/'fixture-retry-finish').touch()
                elif command['action'] == 'release-retry-lock': (paths.root/'fixture-retry-release').touch()
                elif command['action'] == 'finish-download': (paths.root/'fixture-download-finish').touch()
                elif command['action'] == 'start-setup': connection.start_setup.set()
                elif command['action'] == 'finish-setup': connection.finish_setup.set()
                elif command['action'] == 'lose-setup-connection':
                    network.unavailable_until = time.monotonic() + 4
                    connection.close()
                elif command['action'] == 'historical-legacy-output':
                    # TEST ONLY historical metadata, not generated media. This
                    # must not certify a newly activated pruned profile.
                    from h3server.domain import JobRequest, GenerationSettings, JobStatus
                    from h3server.store import OutputMetadata
                    job = store.accept_job(JobRequest('TEST ONLY legacy history', settings=GenerationSettings()), capabilities=CAPABILITIES)
                    store.transition_job(job.job_id, JobStatus.RUNNING)
                    store.transition_job(job.job_id, JobStatus.COMPLETED,
                        output=OutputMetadata('video.mp4', 1, 'video/mp4'))
                elif command['action'] == 'expire-output': store.expire_job_output(command['job_id'])
                elif command['action'] == 'delete-job':
                    with store._transaction(write=True) as db:
                        db.execute('DELETE FROM jobs WHERE job_id=?', (command['job_id'],))
                else: raise AssertionError('TEST ONLY unsupported control')
                print(json.dumps({'controlled': command['action']}), flush=True)
        threading.Thread(target=controls, daemon=True).start()
        listener.bind(('127.0.0.1',0)); origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
        app = create_app(state_dir=Path(temporary)/'trust', origin=origin, connection=connection, release=bundle(Path(temporary)))
        # An unmistakable fixture label, without production fake-backend switches.
        app.router.routes[:] = [route for route in app.router.routes if getattr(route,'path',None) != '/']
        @app.get('/', response_class=HTMLResponse)
        def root():
            html = files('h3server').joinpath('web/index.html').read_text()
            return HTMLResponse(html.replace('<body>', '<body><p role="note">TEST ONLY — mocked SSH/runtime; fixture output is not H3 video.</p>'))
        print(json.dumps({'launch':origin+'/#token='+app.state.session.bootstrap_token, 'fixture':'TEST ONLY'}), flush=True)
        try:
            uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False)).run(sockets=[listener])
        finally:
            server.should_exit = True; thread.join(10)


if __name__ == '__main__': main()
