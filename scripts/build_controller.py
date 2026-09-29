"""Build a pure remote wheel first, then the native self-contained controller.

No model/runtime dependencies are installed. The embedded digest is an integrity
pin within the distribution, not independent release provenance; authenticate the
executable/checksum through your trusted distribution channel.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def verify_wheel(path):
    from h3server.controller.deploy import ReleaseBundle, license_document
    from h3server.backends import load_manifest
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    ReleaseBundle(path, digest)
    with zipfile.ZipFile(path) as archive:
        required = ['h3server/__main__.py', 'h3server/controller/target.py',
                    'h3server/remote/__main__.py', 'h3server/remote/worker.py',
                    'h3server/backends/manifest.json', 'h3server/backends/LICENSE.h3.txt',
                    'h3server/web/index.html', 'h3server/web/app.js', 'h3server/web/styles.css']
        for name in required:
            assert archive.read(name), name
        assert json.loads(archive.read('h3server/backends/manifest.json')) == load_manifest()
        assert archive.read('h3server/backends/LICENSE.h3.txt').decode() == license_document()
        assert not any('/payload/' in name for name in archive.namelist()), 'recursive payload'
    return digest


def main():
    root = Path(__file__).resolve().parents[1]
    payload = root / 'build/controller-payload'
    shutil.rmtree(payload, ignore_errors=True)
    payload.mkdir(parents=True)
    subprocess.run([sys.executable, '-m', 'build', '--wheel', '--outdir', str(payload)], cwd=root, check=True)
    wheel, = payload.glob('*.whl')
    digest = verify_wheel(wheel)
    (payload / 'release.json').write_text(json.dumps({'wheel': wheel.name, 'sha256': digest}, sort_keys=True), encoding='utf-8')
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm',
                    'packaging/controller.spec'], cwd=root, check=True)
    executable = root / 'dist' / ('h3-controller.exe' if sys.platform == 'win32' else 'h3-controller')
    checksum = hashlib.sha256(executable.read_bytes()).hexdigest()
    (root / 'dist/SHA256SUMS').write_text(f'{checksum}  {executable.name}\n', encoding='utf-8')
    print(f'Built native {sys.platform}: {executable}; verified wheel {digest}')


if __name__ == '__main__':
    main()
