"""TEST ONLY: real controller + daemon/store/worker with mocked SSH/runtime IO.

Never connects SSH, installs packages, downloads weights, or claims H3 output.
The fixture 'video' bytes are not a playable/generated MP4.
"""
from dataclasses import replace
import hashlib
from importlib.resources import files
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

import uvicorn
from fastapi.responses import HTMLResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from controller_fixture import BrowserNetwork, BrowserTransport
from test_deploy import LocalSFTP, bundle
from test_install_plan import facts
from test_installer import FakeIO
from test_worker import fixture_backend
from h3server.controller.app import create_app
from h3server.controller.ssh import SSHConnection
from h3server.installer.recovery import service_plan, Recovery
from h3server.installer.planner import Approval
from h3server.installer.runner import Installer
from h3server.paths import AppPaths
from h3server.probe import CommandResult
from h3server.remote.app import create_app as remote_app, load_token
from h3server.remote.worker import Worker
from h3server.store import Store, canonical_plan_digest


class DirectTransport(BrowserTransport):
    def open_channel(self, kind, destination, source, timeout):
        assert kind == 'direct-tcpip' and destination == ('127.0.0.1', 8765)
        channel = socket.create_connection(('127.0.0.1', self.network.api_port), timeout)
        self.channels.append(channel)
        return channel


class Network(BrowserNetwork):
    def socket(self, address, timeout):
        result = super().socket(address, timeout)
        self.outputs["printf '%s' \"$HOME\""] = str(self.home).encode()
        return result
    def transport(self, sock):
        result = DirectTransport(self, sock); self.transports.append(result); return result


class FixtureSSH(SSHConnection):
    """Mock only target SSH commands/SFTP; compose real plans/installer/store."""
    def open_sftp(self):
        self._require_authentication()
        return LocalSFTP('/')
    def inspect_install(self, **kwargs):
        self._require_authentication()
        return facts()
    def recovery(self, *, root, artifact, digest, action, **kwargs):
        self._require_authentication()
        paths = AppPaths(root)
        plan = json.loads((paths.bootstrap/digest/'plan.json').read_text())
        if action == 'service-plan':
            return CommandResult(json.dumps(service_plan(paths, plan, uid=1000)), returncode=0)
        store = Store(paths.state/'setup.sqlite3')
        runner = Installer(paths, store, io=FakeIO())
        if action == 'prepare':
            approval = Approval(**json.loads((paths.bootstrap/digest/'approval.json').read_text()))
            assert kwargs['service_digest'] == canonical_plan_digest(service_plan(paths, plan, uid=1000))
            setup = runner.create(plan)
            runner.run(setup.setup_id, approval)
            return CommandResult(returncode=0)
        raise AssertionError('TEST ONLY unsupported SSH action')
    def deployment_action(self, *, root, artifact, digest, action, data):
        self._require_authentication()
        from h3server.controller.target import snapshot
        paths = AppPaths(root)
        if action == 'snapshot': return snapshot(paths, Store(paths.state/'setup.sqlite3'), data['after'])
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
        store = Store(paths.state/'remote.sqlite')
        worker = Worker(store, paths, backend=backend, min_free_bytes=0)
        daemon = remote_app(store, worker, token=load_token(paths), heartbeat_seconds=.2)
        remote_listener.bind(('127.0.0.1',0))
        server = uvicorn.Server(uvicorn.Config(daemon, log_level='critical', access_log=False))
        thread = threading.Thread(target=lambda: server.run(sockets=[remote_listener]), daemon=True)
        thread.start()
        while not server.started: time.sleep(.01)
        network = Network(); network.home = home; network.api_port = remote_listener.getsockname()[1]
        network.outputs.update({'LC_ALL=C uname -s':b'Linux', 'LC_ALL=C uname -m':b'x86_64',
            'LC_ALL=C cat /etc/os-release':b'ID=ubuntu\nVERSION_ID="24.04"\n'})
        connection = FixtureSSH(Path(temporary)/'trust', socket_factory=network.socket, transport_factory=network.transport)
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
