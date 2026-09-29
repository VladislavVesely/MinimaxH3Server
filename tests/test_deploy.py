"""Deployment integration: real stores/files, only target transport/runtime IO replaced."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import zipfile

import pytest
from fastapi.testclient import TestClient

from h3server.controller.deploy import ReleaseBundle, Deployment
from h3server.controller.ssh import ControllerError
from h3server.installer.planner import Approval
from h3server.store import canonical_plan_digest
from test_controller_security import ORIGIN, API, bootstrap


def wheel(tmp_path, extra=None):
    path = tmp_path / 'h3server-0.1.0-py3-none-any.whl'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('h3server/__init__.py', '')
        z.writestr('h3server-0.1.0.dist-info/WHEEL', 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        if extra:
            z.writestr(extra, 'do not ship')
    return path


def bundle(tmp_path):
    path = wheel(tmp_path)
    return ReleaseBundle(path, hashlib.sha256(path.read_bytes()).hexdigest())


def test_release_requires_external_digest_and_confined_distribution(tmp_path):
    path = wheel(tmp_path)
    with pytest.raises(ValueError):
        ReleaseBundle(path, '0' * 64)
    release = bundle(tmp_path)
    assert release.artifact.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    for forbidden in ('server.txt', 'tests/fixture.py', 'h3server/.venv/key', '../evil', 'h3server/__pycache__/secret.pyc', 'h3server/node_modules/key'):
        path = wheel(tmp_path, forbidden)
        with pytest.raises(ValueError):
            ReleaseBundle(path, hashlib.sha256(path.read_bytes()).hexdigest())


def test_deployment_approval_cannot_be_forged_or_reused(tmp_path):
    from test_install_plan import facts
    from h3server.installer.planner import build_plan
    release = bundle(tmp_path)
    class SSH:
        def __init__(self):
            import threading
            self.lock = threading.RLock()
        def status(self): return {'state': 'authenticated'}
        def remote_home(self): return '/home/test'
    deploy = Deployment(SSH(), release)
    plan = build_plan(facts(), profile='h3-bf16-cpu-offload-experimental', artifact=release.artifact)
    deploy.plan = plan
    deploy.root = '/home/test/MinimaxH3Server'
    with pytest.raises(ControllerError):
        deploy.install(Approval(canonical_plan_digest(plan)), service_digest=None)
    with pytest.raises(ControllerError):
        deploy.install(Approval('0'*64, True, True, True), service_digest=None)


def test_maintenance_reject_drain_cancel_and_restart(tmp_path):
    from h3server.domain import JobRequest, GenerationSettings, JobStatus
    from h3server.remote.worker import BackendUnavailable
    from test_remote_api import service
    from test_worker import fixture_backend
    store, worker, _ = service(tmp_path, backend=fixture_backend(tmp_path), min_free_bytes=0)
    request = JobRequest('test only', settings=GenerationSettings())
    job = worker.submit(request)
    operation = 'a' * 32
    with pytest.raises(ValueError):
        worker.begin_maintenance(operation, policy='reject', cancel_confirmed=False)
    assert not worker.maintenance_status()['active']
    worker.begin_maintenance(operation, policy='drain', cancel_confirmed=False)
    with pytest.raises(BackendUnavailable): worker.submit(request)
    assert worker.maintenance_status()['pending'] == 1
    with pytest.raises(ValueError): worker.begin_maintenance(operation, policy='cancel', cancel_confirmed=False)
    with pytest.raises(ValueError): worker.end_maintenance('b'*32)
    # Durable ownership survives daemon restart; no timed lease can reopen writes.
    from h3server.remote.worker import Worker
    reopened = Worker(store, worker.paths, backend=fixture_backend(tmp_path), min_free_bytes=0)
    assert reopened.maintenance_status()['active']
    worker.begin_maintenance(operation, policy='cancel', cancel_confirmed=True)
    assert store.get_job(job.job_id).status == JobStatus.CANCELLED
    worker.end_maintenance(operation)
    worker.end_maintenance(operation)
    assert not worker.maintenance_status()['active']


def test_controller_deploy_security_and_no_artifact_is_not_ready(tmp_path):
    from h3server.controller.app import create_app
    app = create_app(state_dir=tmp_path, origin=ORIGIN)
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.post(API+'/setup/install', json={}).status_code == 403
        headers = bootstrap(app, client)
        response = client.post(API+'/setup/plan', json={'profile':'h3-bf16-cpu-offload-experimental'}, headers=headers)
        assert response.status_code == 409
        assert response.json()['error']['code'] in {'release_unavailable','authentication_required'}
        assert client.get(API+'/remote/health').status_code == 409
        assert client.get(API+'/remote/config/remote-token').status_code == 404


class LocalSFTP:
    """TEST ONLY SFTP boundary backed by a confined temporary real filesystem."""
    def __init__(self, base): self.base = Path(base)
    def path(self, value):
        assert value.startswith('/') and '..' not in Path(value).parts
        return self.base / value.lstrip('/')
    def lstat(self, path): return self.path(path).lstat()
    def mkdir(self, path, mode): self.path(path).mkdir(mode=mode)
    def chmod(self, path, mode): self.path(path).chmod(mode)
    def open(self, path, mode): return self.path(path).open('xb' if mode == 'wx' else mode)
    def rename(self, a, b):
        if self.path(b).exists(): raise FileExistsError()
        self.path(a).rename(self.path(b))
    def posix_rename(self, a, b): self.path(a).replace(self.path(b))
    def remove(self, path): self.path(path).unlink()
    def close(self): pass


def test_sftp_staging_is_private_immutable_and_rejects_links(tmp_path):
    from h3server.controller.deploy import _write, _read
    sftp = LocalSFTP(tmp_path)
    _write(sftp, '/home/test/MinimaxH3Server/releases/v1/wheel', b'verified', immutable=True)
    path = sftp.path('/home/test/MinimaxH3Server/releases/v1/wheel')
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert _read(sftp, '/home/test/MinimaxH3Server/releases/v1/wheel') == b'verified'
    with pytest.raises(ControllerError):
        _write(sftp, '/home/test/MinimaxH3Server/releases/v1/wheel', b'changed', immutable=True)
    path.unlink(); path.symlink_to(tmp_path / 'secret')
    with pytest.raises(ControllerError): _write(sftp, '/home/test/MinimaxH3Server/releases/v1/wheel', b'bad')
    with pytest.raises(ControllerError): _write(sftp, '/home/test/MinimaxH3Server/releases/v1/wheel/escape', b'bad')
    assert not (tmp_path/'secret').exists()


def staged_target(tmp_path):
    from h3server.paths import AppPaths
    from h3server.installer.planner import build_plan
    from test_install_plan import facts
    from h3server.installer.steps import atomic_text
    release = bundle(tmp_path)
    paths = AppPaths(tmp_path/'target')
    for directory in paths.directories(): directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = paths.releases / release.artifact.release_id
    target.mkdir()
    (target / release.artifact.filename).write_bytes(release.data)
    plan = build_plan(facts(), profile='h3-bf16-cpu-offload-experimental', artifact=release.artifact)
    digest = canonical_plan_digest(plan)
    env = paths.environments / digest
    env.mkdir(); (env / 'bin').mkdir(); (env / 'bin/python').write_text('TEST ONLY')
    atomic_text(env/'readiness.json', '{"environment_ready":true}')
    atomic_text(env/'resolved.txt', 'TEST ONLY runtime==1\n')
    return paths, plan


class RuntimeIO:
    """TEST ONLY: no subprocess, GPU, native services or real daemon restart."""
    def __init__(self, paths, *, previous=True, fail_health=False, busy=False):
        self.paths, self.previous, self.fail_health, self.busy = paths, previous, fail_health, busy
        self.calls = []; self.gated = False; self.health_calls = 0; self.operation = 'a'*32
    def preflight(self, python): self.calls.append(('preflight', str(python)))
    def api(self, method, path, data=None):
        self.calls.append((method, path, data))
        if path == 'health':
            if not self.previous: raise ConnectionRefusedError()
            return {'service':'ok'}
        if path == 'maintenance/begin':
            if self.busy and data['policy'] == 'reject': raise RuntimeError('busy')
            self.gated = True; self.operation = data['operation']
            return {'active':True}
        if path == 'maintenance': return {'idle':not self.busy, 'active':self.gated, 'operation':self.operation}
        if path == 'maintenance/end': self.gated = False; return {'active':False}
    def service(self, command): self.calls.append(('service', *command))
    def health(self):
        self.health_calls += 1
        if self.fail_health and self.health_calls == 1: raise RuntimeError('TEST health failed')


def previous_release(paths):
    from h3server.backends.base import atomic_json
    (paths.releases/'old').mkdir()
    paths.current.symlink_to('releases/old')
    previous = {'release':'old', 'environment':'old-environment', 'unit':'TEST ONLY previous unit'}
    atomic_json(paths, 'state/active-release.json', previous)
    return previous


def test_atomic_activation_health_failure_restores_release_environment_db_and_models(tmp_path):
    import sqlite3
    from h3server.controller.target import Activation, active_release
    paths, plan = staged_target(tmp_path)
    old = previous_release(paths)
    (paths.models/'keep.safetensors').write_bytes(b'not real weights')
    from h3server.store import Store
    db = Store(paths.state/'remote.sqlite')
    io = RuntimeIO(paths, fail_health=True)
    with pytest.raises(RuntimeError):
        Activation(paths, io=io).run(plan, 'a'*32, policy='reject')
    assert paths.current.resolve() == paths.releases/'old'
    assert active_release(paths) == old
    assert (paths.config/'systemd/h3server.service').read_text() == old['unit']
    assert (paths.models/'keep.safetensors').read_bytes() == b'not real weights'
    assert not io.gated
    assert list(paths.state.glob('remote-*.backup.sqlite'))
    Store(db.path)  # exact schema still accepted
    assert json.loads((paths.state/'activation.json').read_text())['phase'] == 'rolled_back'


def test_activation_busy_reject_and_drain_timeout_release_gate(tmp_path):
    from h3server.controller.target import Activation
    paths, plan = staged_target(tmp_path)
    previous_release(paths)
    io = RuntimeIO(paths, busy=True)
    with pytest.raises(RuntimeError): Activation(paths, io=io).run(plan, 'a'*32, policy='reject')
    assert not io.gated
    assert paths.current.resolve().name == 'old'
    now = [0]
    def wait(amount): now[0] += amount
    with pytest.raises(RuntimeError):
        Activation(paths, io=io, wait=wait, clock=lambda:now[0]).run(plan, 'b'*32, policy='drain', drain_timeout=1)
    assert not io.gated
    assert not any(call == ('service','stop','h3server.service') for call in io.calls)
    with pytest.raises(ValueError): Activation(paths, io=io).run(plan, 'c'*32, policy='cancel')


def test_activation_success_and_interrupted_journal_repair(tmp_path):
    from h3server.controller.target import Activation, active_release
    paths, plan = staged_target(tmp_path)
    old = previous_release(paths)
    io = RuntimeIO(paths)
    result = Activation(paths, io=io).run(plan, 'a'*32, policy='drain')
    assert result == {'daemon_ready':True, 'installation_ready':True, 'model_ready':False, 'generation_tested':False}
    assert paths.current.resolve().name == plan['artifact']['release_id']
    assert active_release(paths)['environment'] == canonical_plan_digest(plan)
    journal = json.loads((paths.state/'activation.json').read_text())
    # Simulate process loss in switching phase; a new process reads durable owner.
    journal['phase'] = 'switching'
    Activation(paths, io=io).rollback(journal)
    assert active_release(paths) == old
    assert not io.gated


def test_maintenance_serializes_concurrent_submit_and_drains_queued_work(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from h3server.domain import JobRequest, GenerationSettings, JobStatus
    from h3server.remote.worker import BackendUnavailable
    from test_remote_api import service
    from test_worker import fixture_backend, wait_status
    store, worker, _ = service(tmp_path, backend=fixture_backend(tmp_path), queue_limit=100, min_free_bytes=0)
    def submit(_):
        try: return worker.submit(JobRequest('TEST ONLY', settings=GenerationSettings()))
        except BackendUnavailable: return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(submit, i) for i in range(16)]
        worker.begin_maintenance('a'*32, policy='drain')
        accepted = [future.result() for future in futures]
    assert submit(None) is None
    worker.start()
    try:
        for job in accepted:
            if job: assert wait_status(store, job.job_id, {JobStatus.COMPLETED,JobStatus.FAILED}, timeout=8).status == JobStatus.COMPLETED
        assert worker.maintenance_status()['idle']
        assert worker.maintenance_status()['active']
        assert worker.begin_maintenance('a'*32, policy='drain')['active']
    finally: worker.stop()
    worker.end_maintenance('a'*32)


def test_pinned_license_is_packaged_complete_and_hash_verified():
    from h3server.controller.deploy import license_document
    from h3server.backends import load_manifest
    text = license_document()
    assert 'Exhibit A' in text
    assert hashlib.sha256(text.encode()).hexdigest() == load_manifest()['sources']['license']['sha256']


def test_proxy_range_auth_and_exact_allowlist_without_public_forwarder(tmp_path, monkeypatch):
    import socket
    import threading
    from test_ssh import trusted_connection
    from h3server.controller.deploy import _write
    from h3server.controller.app import create_app
    network, connection = trusted_connection(tmp_path)
    sftp = LocalSFTP(tmp_path/'target'); sftp.base.mkdir()
    token = 'TEST_ONLY_DAEMON_TOKEN_' + 'x'*40
    _write(sftp, '/home/test/MinimaxH3Server/config/remote-token', token.encode())
    monkeypatch.setattr(connection, 'open_sftp', lambda: sftp)
    captured = []
    threads = []
    def channel():
        local, remote = socket.socketpair()
        def serve():
            with remote:
                request = b''
                while b'\r\n\r\n' not in request: request += remote.recv(4096)
                captured.append(request)
                remote.sendall(b'HTTP/1.1 206 Partial Content\r\nContent-Length: 4\r\nContent-Type: video/mp4\r\nContent-Range: bytes 2-5/9\r\nAccept-Ranges: bytes\r\nSet-Cookie: forbidden\r\nLocation: http://untrusted/\r\n\r\nTEST')
        thread = threading.Thread(target=serve); thread.start(); threads.append(thread)
        return local
    monkeypatch.setattr(connection, 'open_api_channel', channel)
    app = create_app(state_dir=tmp_path/'local', origin=ORIGIN, connection=connection)
    with TestClient(app, base_url=ORIGIN) as client:
        route = API+'/remote/jobs/'+'a'*32+'/video'
        assert client.get(route).status_code == 401
        headers = bootstrap(app, client)
        response = client.get(route, headers={'Range':'bytes=2-5'})
        assert response.status_code == 206 and response.content == b'TEST'
        assert response.headers['content-range'] == 'bytes 2-5/9'
        assert 'set-cookie' not in response.headers and 'location' not in response.headers
        assert token not in response.text
        assert client.get(route, headers={'Range':'bytes=0-1,2-3'}).status_code == 416
        assert client.get(API+'/remote/maintenance').status_code == 404
        assert client.post(API+'/remote/jobs', json={'prompt':'test'}).status_code == 403
        assert len(captured) == 1
        wire = captured[0].decode()
        assert 'Authorization: Bearer '+token in wire and 'Range: bytes=2-5' in wire
        assert 'Origin:' not in wire and 'Cookie:' not in wire
    for thread in threads: thread.join(2)


def test_ssh_fixed_deployment_command_rechecks_wheel_and_sends_only_stdin(tmp_path):
    from test_ssh import trusted_connection
    from h3server.probe import CommandResult
    network, connection = trusted_connection(tmp_path)
    calls = []
    connection._execute = lambda command, timeout, **kwargs: (calls.append((command,timeout,kwargs)) or CommandResult('{"state":"accepted"}',returncode=0))
    release = bundle(tmp_path)
    assert connection.deployment_action(root='/home/test/MinimaxH3Server', artifact=release.artifact,
        digest='b'*64, action='activate', data={'plan_digest':'b'*64,'activation_approved':True})['state']=='accepted'
    command,timeout,options = calls[0]
    assert 'sha256sum -c -' in command and release.artifact.sha256 in command
    assert 'activation_approved' not in command
    assert json.loads(options['stdin'])['activation_approved'] is True
    for action in ('shell', '../activate', 'download; reboot'):
        with pytest.raises(ValueError):
            connection.deployment_action(root='/home/test',artifact=release.artifact,digest='b'*64,action=action,data={})
    connection.close()


def test_target_detached_acceptance_is_never_completion_and_replay_marks_interruption(tmp_path, monkeypatch, capsys):
    import io
    from types import SimpleNamespace
    import sys
    from h3server.backends.base import atomic_json
    from h3server.controller import target
    from h3server.installer.runner import Installer
    from h3server.store import Store
    from test_installer import FakeIO
    paths, plan = staged_target(tmp_path)
    digest = canonical_plan_digest(plan)
    approval = Approval(digest,True,True,True)
    atomic_json(paths, f'bootstrap/{digest}/plan.json', plan)
    atomic_json(paths, f'bootstrap/{digest}/approval.json', asdict(approval))
    store = Store(paths.state/'setup.sqlite3')
    runner = Installer(paths,store,io=FakeIO())
    setup = runner.create(plan); runner.run(setup.setup_id,approval)
    monkeypatch.setattr(target,'_detach',lambda fd:False)
    monkeypatch.setattr(sys,'argv',['target','--root',str(paths.root),'--digest',digest,'activate'])
    monkeypatch.setattr(sys,'stdin',SimpleNamespace(buffer=io.BytesIO(json.dumps({'plan_digest':digest,'activation_approved':True,'policy':'reject'}).encode())))
    target.main()
    assert json.loads(capsys.readouterr().out)['state']=='accepted'
    assert not paths.current.exists()
    view=target.snapshot(paths,store,0)
    assert view['operation']['state']=='interrupted'  # test detacher did not run anything
    assert view['active_release'] is None


def test_target_crafted_download_or_activation_cannot_skip_consent(tmp_path, monkeypatch):
    import io, sys
    from types import SimpleNamespace
    from h3server.backends.base import atomic_json
    from h3server.controller import target
    paths, plan = staged_target(tmp_path)
    digest = canonical_plan_digest(plan)
    atomic_json(paths,f'bootstrap/{digest}/plan.json',plan)
    atomic_json(paths,f'bootstrap/{digest}/approval.json',asdict(Approval(digest,True,True,True)))
    for action, payload in [('download',{'plan_digest':digest}), ('activate',{'plan_digest':digest,'policy':'cancel','activation_approved':True})]:
        monkeypatch.setattr(sys,'argv',['target','--root',str(paths.root),'--digest',digest,action])
        monkeypatch.setattr(sys,'stdin',SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode())))
        with pytest.raises(ValueError): target.main()
        assert not (paths.state/'operation.json').exists()
        assert not (paths.config/'h3-consent.json').exists()


def test_interrupted_pre_switch_repair_only_releases_gate_without_stopping_jobs(tmp_path):
    from h3server.controller.target import Activation
    from h3server.backends.base import atomic_json
    paths, plan = staged_target(tmp_path)
    old = previous_release(paths)
    io = RuntimeIO(paths, busy=True); io.gated = True
    journal = {'operation':'a'*32, 'previous':old, 'phase':'draining'}
    atomic_json(paths,'state/activation.json',journal)
    Activation(paths,io=io).rollback(journal)
    assert not any(call[0]=='service' for call in io.calls)
    assert not io.gated
    assert paths.current.resolve().name == 'old'


def test_interrupted_activation_journal_cannot_be_overwritten_by_retry(tmp_path):
    from h3server.controller.target import Activation
    from h3server.backends.base import atomic_json, read_json
    paths, plan = staged_target(tmp_path)
    old = previous_release(paths)
    journal={'operation':'a'*32,'previous':old,'phase':'switching'}
    atomic_json(paths,'state/activation.json',journal)
    io=RuntimeIO(paths)
    with pytest.raises(ValueError,match='repair'):
        Activation(paths,io=io).run(plan,'b'*32,policy='reject')
    assert read_json(paths,'state/activation.json')==journal
    assert not io.calls


def test_python_absent_prerequisite_progress_is_read_over_sftp(tmp_path):
    from contextlib import contextmanager
    from h3server.controller.deploy import _write
    from test_install_plan import facts
    from h3server.installer.planner import build_plan
    import threading
    release=bundle(tmp_path)
    sftp=LocalSFTP(tmp_path/'remote');sftp.base.mkdir()
    class SSH:
        lock=threading.RLock()
        def status(self):return {'state':'authenticated'}
        def remote_home(self):return '/home/test'
        @contextmanager
        def private_sftp(self):yield sftp
        def deployment_action(self,**kwargs):raise AssertionError('Python does not exist yet')
    deployment=Deployment(SSH(),release)
    deployment.root='/home/test/MinimaxH3Server'
    deployment.plan=build_plan(facts(python_ready=False,selector_available=False),profile='h3-bf16-cpu-offload-experimental',artifact=release.artifact)
    digest=canonical_plan_digest(deployment.plan)
    _write(sftp,f'{deployment.root}/bootstrap/{digest}/status',b'waiting_for_replan\n')
    view=deployment.snapshot()
    assert view['setups'][0]['state']=='waiting_for_replan'
    assert view['setups'][0]['plan_digest']==digest


@pytest.mark.parametrize('phase,final', [('committed','complete'),('rollback_committed','rolled_back')])
def test_interrupted_commit_finalizes_gate_without_restoring_database_or_stopping_jobs(tmp_path,phase,final):
    from h3server.controller.target import Activation
    from h3server.backends.base import atomic_json,read_json
    paths,plan=staged_target(tmp_path)
    old=previous_release(paths)
    io=RuntimeIO(paths);io.gated=True
    journal={'operation':'a'*32,'previous':old,'phase':phase}
    atomic_json(paths,'state/activation.json',journal)
    Activation(paths,io=io).rollback(journal)
    assert not any(call[0]=='service' for call in io.calls)
    assert not io.gated
    assert read_json(paths,'state/activation.json')['phase']==final


def test_paramiko_http_response_keeps_channel_open_after_connection_close():
    """Unlike socket.makefile, Paramiko ChannelFile does not duplicate an fd."""
    import io
    from http.client import HTTPResponse
    from h3server.controller.proxy import _ChannelSocket
    class Channel:
        closed=False
        def makefile(self,mode):
            self.file=io.BytesIO(b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 9\r\n\r\nTEST ONLY')
            return self.file
        def close(self):
            self.closed=True
            if not self.file.closed:self.file.seek(0,2)
    channel=Channel();sock=_ChannelSocket(channel)
    response=HTTPResponse(sock);response.begin()
    sock.close()  # HTTPConnection.getresponse does this on will_close responses.
    assert not channel.closed
    assert response.read()==b'TEST ONLY'
    response.close()
    assert channel.closed


def test_target_bootstrap_import_needs_only_stdlib_before_environment_install(tmp_path):
    import subprocess,sys,os
    source=str(Path(__file__).resolve().parents[1]/'src')
    result=subprocess.run([sys.executable,'-S','-W','error','-m','h3server.controller.target','--help'],
        cwd=tmp_path,env={**os.environ,'PYTHONPATH':source},capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr


def test_sftp_directory_refuses_parent_traversal_before_any_mutation(tmp_path):
    from h3server.controller.deploy import _directory
    class NoIO:
        def __getattr__(self,name):raise AssertionError('unexpected SFTP IO')
    for path in ('relative/path','/home/test/../outside','/home/./test'):
        with pytest.raises(ControllerError,match='unsafe_remote_path'):_directory(NoIO(),path)
