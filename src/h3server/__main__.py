"""Native/source controller launcher. No SSH credentials are accepted in argv."""
import argparse
from contextlib import ExitStack
from importlib.metadata import version
from importlib.resources import as_file, files
import json
import multiprocessing
import os
from pathlib import Path
import socket
import sys
import webbrowser
import zipfile

import uvicorn

from h3server.controller.app import create_app
from h3server.controller.deploy import ReleaseBundle


class PrivateParser(argparse.ArgumentParser):
    def error(self, message):
        raise SystemExit('invalid controller arguments; use --help')


def state_directory():
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'H3Server'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/H3Server'
    return Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'h3server'


class BrowserServer(uvicorn.Server):
    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.started:
            self.launch()


def main(argv=None):
    multiprocessing.freeze_support()
    parser = PrivateParser(description='MiniMax H3 local controller. Deployment always requires browser approvals.')
    parser.add_argument('command', nargs='?', choices=['deploy'], help='open the deployment wizard (also the default)')
    parser.add_argument('--version', action='version', version='h3-controller ' + version('h3server'))
    parser.add_argument('--no-browser', action='store_true', help='print the one-use launch URL instead of opening a browser')
    parser.add_argument('--state-dir', type=Path, default=state_directory())
    parser.add_argument('--port', type=int, default=0, help='loopback port, default: reserve an available port')
    parser.add_argument('--wheel', type=Path, help='override bundled release with an operator-verified wheel')
    parser.add_argument('--sha256', help='trusted SHA256 for --wheel (both required together)')
    args = parser.parse_args(argv)
    if (args.wheel is None) != (args.sha256 is None):
        raise SystemExit('--wheel and --sha256 must be supplied together')
    if not 0 <= args.port <= 65535:
        raise SystemExit('invalid loopback port')
    with ExitStack() as stack:
        wheel, digest = args.wheel, args.sha256
        if wheel is None:
            payload = files('h3server').joinpath('payload')
            try:
                manifest = json.loads(payload.joinpath('release.json').read_text(encoding='utf-8'))
                name = manifest['wheel']
                if not isinstance(name, str) or Path(name).name != name or '/' in name or '\\' in name:
                    raise ValueError('invalid wheel name')
                wheel = stack.enter_context(as_file(payload.joinpath(name)))
                digest = manifest['sha256']
            except (OSError, ValueError, KeyError, TypeError):
                raise SystemExit('bundled release unavailable; build a release wheel and pass --wheel PATH --sha256 TRUSTED_PIN') from None
        try:
            release = ReleaseBundle(wheel, digest)
        except (OSError, ValueError, zipfile.BadZipFile):
            raise SystemExit('release verification failed') from None
        listener = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
        try:
            # Do not release/rebind: the same reserved socket belongs to uvicorn.
            if os.name == 'nt':
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(('127.0.0.1', args.port))
            listener.listen(128)
        except OSError:
            raise SystemExit('cannot reserve loopback port; choose another --port') from None
        origin = 'http://127.0.0.1:' + str(listener.getsockname()[1])
        app = create_app(state_dir=args.state_dir, origin=origin, release=release)
        url = origin + '/#token=' + app.state.session.bootstrap_token
        def launch():
            opened = False
            if not args.no_browser:
                try:
                    opened = webbrowser.open(url)
                except (webbrowser.Error, OSError):
                    pass
            if not opened:
                print(url, flush=True)  # Explicit one-use local secret, never a remote credential.
        server = BrowserServer(uvicorn.Config(app, access_log=False, log_level='critical',
                                             loop='asyncio', http='h11', ws='none'))
        server.launch = launch
        try:
            server.run(sockets=[listener])
        except KeyboardInterrupt:
            # Uvicorn re-raises Ctrl+C after its lifespan cleanup. Frozen apps
            # must not turn this normal shutdown into a bootloader error dialog.
            pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
