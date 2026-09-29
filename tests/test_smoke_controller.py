"""Real CArchive/ZIP fixtures, not native Windows execution or model generation."""
import hashlib
import importlib.util
import io
import json
import ntpath
import os
from pathlib import Path
import posixpath
from types import SimpleNamespace
import zipfile

import pytest


@pytest.fixture(params=[posixpath, ntpath], ids=['posix', 'windows'])
def embedded_payload(request, tmp_path, monkeypatch):
    writers = pytest.importorskip('PyInstaller.archive.writers')
    from PyInstaller.archive.readers import CArchiveReader

    spec = importlib.util.spec_from_file_location('smoke_controller',
        Path(__file__).resolve().parents[1] / 'scripts/smoke_controller.py')
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    # Simulate only native path spelling. Filesystem IO and CArchive/ZIP parsing
    # remain real; do not replace the global os.path used by pytest/the host.
    native_os = SimpleNamespace(**{**vars(os), 'path': request.param})
    monkeypatch.setattr(writers, 'os', native_os)
    monkeypatch.setattr(writers, 'is_win', request.param is ntpath)
    monkeypatch.setattr(smoke, 'os', native_os)

    resources = {f'h3server/{name}': f'fixture: {name}'.encode() for name in [
        'web/index.html', 'web/app.js', 'web/styles.css',
        'backends/manifest.json', 'backends/LICENSE.h3.txt']}
    wheel_bytes = io.BytesIO()
    with zipfile.ZipFile(wheel_bytes, 'w') as wheel:
        for name, data in resources.items():
            wheel.writestr(name, data)  # ZIP members are POSIX on every host.
        wheel.writestr('h3server/controller/target.py', '# fixture only\n')
    wheel_data = wheel_bytes.getvalue()
    manifest = {'wheel': 'h3server-0.1.0-py3-none-any.whl',
                'sha256': hashlib.sha256(wheel_data).hexdigest()}

    def build(corruption=None):
        selected = dict(resources)
        if corruption == 'resource':
            selected['h3server/web/app.js'] = b'tampered resource'
        if corruption == 'digest':
            manifest['sha256'] = '0' * 64
        selected['h3server/payload/' + manifest['wheel']] = wheel_data
        selected['h3server/payload/release.json'] = json.dumps(manifest).encode()
        entries = []
        for index, (name, data) in enumerate(selected.items()):
            source = tmp_path / f'payload-{index}'
            source.write_bytes(data)
            entries.append((name, str(source), True, 'x'))
        archive_path = tmp_path / 'controller.pkg'
        writers.CArchiveWriter(str(archive_path), entries, 'test-python-library')
        archive = CArchiveReader(str(archive_path))
        assert set(archive.toc) == {request.param.normpath(name) for name in selected}
        return archive_path

    return smoke, build


def test_verify_embedded_native_carchive_names_preserve_posix_wheel_members(embedded_payload, capsys):
    smoke, build = embedded_payload
    smoke.verify_embedded(build())
    assert 'Embedded release wheel, web, manifest and full license verified' in capsys.readouterr().out


@pytest.mark.parametrize('corruption', ['digest', 'resource'])
def test_verify_embedded_native_carchive_names_keep_integrity_checks(embedded_payload, corruption):
    smoke, build = embedded_payload
    with pytest.raises(AssertionError):
        smoke.verify_embedded(build(corruption))
