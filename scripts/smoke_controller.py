"""Local-only native launch/security/assets test. No SSH or generation fixtures."""
import argparse
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import queue
import signal
import socket
import subprocess
import tempfile
import threading
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPCookieProcessor
import zipfile


def verify_embedded(executable, source_root=None):
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(executable))
    def extract(name):
        # CArchive keys use native separators; wheel ZIP members remain POSIX.
        return archive.extract(os.path.normpath(name))
    manifest = json.loads(extract('h3server/payload/release.json'))
    wheel = extract('h3server/payload/' + manifest['wheel'])
    assert hashlib.sha256(wheel).hexdigest() == manifest['sha256']
    import io
    with zipfile.ZipFile(io.BytesIO(wheel)) as release:
        for name in ['web/index.html', 'web/app.js', 'web/styles.css', 'backends/manifest.json', 'backends/LICENSE.h3.txt']:
            assert release.read('h3server/' + name) == extract('h3server/' + name)
        for name in ['controller/target.py', 'backends/catalog.py', 'backends/comfy_runtime.py', 'backends/pruned.py']:
            assert 'h3server/' + name in release.namelist()
        if source_root is not None:
            for path in (source_root / 'h3server').rglob('*'):
                if path.is_file() and path.suffix in {'.py', '.json', '.txt', '.html', '.js', '.css'}:
                    name = path.relative_to(source_root).as_posix()
                    assert release.read(name) == path.read_bytes(), 'stale embedded source: ' + name
            print('Embedded remote wheel equals current application source (including pruned runtime/catalog)')
    print('Embedded release wheel, web, manifest and full license verified')


def smoke(command):
    with tempfile.TemporaryDirectory(prefix='h3-native-smoke-') as temp:
        env = {k: v for k, v in os.environ.items() if k not in {'PYTHONPATH', 'PYTHONHOME'}}
        env['PATH'] = temp  # No discoverable local Python/Node or checkout tools.
        process = subprocess.Popen([*command, '--no-browser', '--state-dir', str(Path(temp) / 'state')],
            cwd=temp, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
        lines = queue.Queue()
        threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
        try:
            launch = lines.get(timeout=60).strip()
            assert launch.startswith('http://127.0.0.1:') and '/#token=' in launch, 'missing launch URL'
            origin = launch.split('/#')[0]
            token = launch.split('#token=')[1]
            jar = http.cookiejar.CookieJar()
            client = build_opener(HTTPCookieProcessor(jar))
            def request(path, body=None, headers=None):
                data = json.dumps(body).encode() if body is not None else None
                req = Request(origin + path, data=data, headers={'Content-Type': 'application/json', **(headers or {})})
                try:
                    response = client.open(req, timeout=5)
                except HTTPError as exc:
                    response = exc
                return response.status, response.headers, response.read()
            status, headers, html = request('/')
            assert status == 200 and b'MiniMax H3' in html
            assert headers['X-Frame-Options'] == 'DENY' and headers['Cache-Control'] == 'no-store'
            for path in ['/assets/app.js', '/assets/styles.css']:
                assert request(path)[0] == 200
            api = '/api/v1/controller'
            assert request(api + '/status')[0] == 401
            assert request('/', headers={'Host': 'evil.example'})[0] == 403
            assert request(api + '/bootstrap', {'token': token}, {'Origin': 'https://evil.example'})[0] == 403
            status, headers, body = request(api + '/bootstrap', {'token': token}, {'Origin': origin})
            assert status == 200 and 'HttpOnly' in headers['Set-Cookie'] and 'SameSite=strict' in headers['Set-Cookie']
            csrf = json.loads(body)['csrf_token']
            assert request(api + '/bootstrap', {'token': token}, {'Origin': origin})[0] == 401
            status, _, body = request(api + '/status')
            assert status == 200 and json.loads(body)['capabilities']['release_available']
            from h3server.controller.deploy import profile_catalog
            status, _, body = request(api + '/profiles')
            assert status == 200 and json.loads(body) == json.loads(json.dumps(profile_catalog()))
            assert b'31,550,953,118' not in html  # totals are catalog-derived, not stale HTML literals
            assert b'id="download-selection"' in html
            print('Native authenticated catalog equals source: both Abiray collections, exact totals/digests, no target IO')
            assert request(api + '/disconnect', {}, {'Origin': origin})[0] == 403
            assert request(api + '/disconnect', {}, {'Origin': origin, 'X-CSRF-Token': csrf})[0] == 200
            assert request(api + '/openapi.json')[0] == 200
            assert request('/assets/../payload/release.json')[0] in {401, 404}
            port = urlsplit(origin).port
        finally:
            process.send_signal(signal.CTRL_BREAK_EVENT if os.name == 'nt' else signal.SIGINT)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise AssertionError('controller did not shut down cleanly')
            process.stdout.close()
            stderr = process.stderr.read()
            process.stderr.close()
        assert not stderr.strip(), stderr
        assert process.returncode in {0, -signal.SIGINT, 130, 0xC000013A}, process.returncode
        with socket.socket() as sock:
            assert sock.connect_ex(('127.0.0.1', port)) != 0
        print('PASS: real loopback launch, assets, one-use bootstrap, Host/Origin/CSRF, authenticated API, clean shutdown; no target contacted')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve()
    for arguments in [['--help'], ['deploy', '--help'], ['--version']]:
        subprocess.run([str(executable), *arguments], check=True, capture_output=True, timeout=60)
    verify_embedded(executable, Path(__file__).resolve().parents[1] / 'src')
    smoke([str(executable)])
    smoke([str(executable), 'deploy'])


if __name__ == '__main__':
    main()
