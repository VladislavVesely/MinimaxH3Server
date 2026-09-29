import pytest
from h3server.domain import GenerationSettings


def test_sparse_presets_preserve_sage_and_steps():
    settings = GenerationSettings(profile='h3-convrot-int8', steps=20, sage_attention=True, sparse_preset='quality')
    assert settings.steps == 20 and settings.sage_attention
    assert len(settings.sparse_artifact_digest) == 64


@pytest.mark.parametrize('extra', [dict(sparse_preset='unknown'), dict(sparse_preset='draft',turbo_lora=True,steps=6),dict(sparse_preset='balanced',profile='h3-convrot-mixed')])
def test_sparse_rejects_unverified_combinations(extra):
    with pytest.raises(ValueError):
        GenerationSettings(**({'profile':'h3-convrot-int8', **extra}))


def test_named_sparse_request_and_legacy_flag_are_unambiguous():
    from dataclasses import asdict
    from h3server.domain import JobRequest
    from h3server.backends.pruned import parse_request
    request=JobRequest('test', settings=GenerationSettings(profile='h3-convrot-int8', sparse_preset='quality'))
    assert parse_request(asdict(request)).settings.sparse_preset == 'quality'
    with pytest.raises(ValueError, match='named sparse_preset'):
        parse_request({**asdict(request), 'sparse_attention': True})


def test_sparse_pin_binding_before_install_io():
    from types import SimpleNamespace
    from h3server.remote.sparse import SparseInstaller
    with pytest.raises(ValueError, match='selection changed'):
        SparseInstaller(SimpleNamespace(paths=None)).start('0'*64)


def test_sparse_archive_is_closed_and_checksum_verified(tmp_path, monkeypatch):
    import hashlib, io, zipfile
    from h3server.paths import AppPaths
    from h3server.backends import sparse
    from h3server.backends.base import atomic_json
    from h3server import _download_transport
    source = b'VALUE = 42\n'
    entry = dict(name='h3_optimizations/__init__.py', size=len(source), sha256=hashlib.sha256(source).hexdigest())
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('H3-Optimizations-' + sparse.REVISION + '/' + entry['name'], source)
        archive.writestr('../../unapproved.py', b'raise RuntimeError()')
    payload = buffer.getvalue()
    monkeypatch.setattr(sparse, 'INVENTORY', [entry])
    monkeypatch.setattr(sparse, 'FILES', [dict(name='source.zip', size=len(payload), sha256=hashlib.sha256(payload).hexdigest())])
    monkeypatch.setattr(sparse, 'TOTAL', len(payload))
    paths = AppPaths(tmp_path/'root'); paths.root.mkdir(mode=0o700)
    atomic_json(paths, sparse.CONSENT, sparse.BINDING)
    class Transport:
        def __init__(self, paths, name, offset, **kwargs):
            assert kwargs['component'] == 'sparse'
            self.body=io.BytesIO(payload); self.status=200; self.headers={'Content-Length':str(len(payload))}
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self,n): return self.body.read(n)
    monkeypatch.setattr(_download_transport,'DownloadTransport',Transport)
    progress=[]
    sparse.download(paths,progress=lambda n,total:progress.append((n,total)))
    assert progress[-1] == (len(payload),len(payload))
    sparse.verify_code(paths)
    assert not (tmp_path/'unapproved.py').exists()
    assert not sparse.installed(paths)  # An archive is not a GPU receipt.
    import importlib.util
    loader=sparse.VerifiedLoader(paths)
    spec=loader.find_spec('h3_optimizations'); module=importlib.util.module_from_spec(spec)
    loader.exec_module(module); assert module.VALUE == 42
    module.__name__ = 'h3_optimizations.native.loader'
    loader.exec_module(module)
    assert module._candidate_paths() == [paths.root/sparse.DIRECTORY/'code/native/bin/libh3_int8_attention.so']
    monkeypatch.setattr(sparse, 'gpu_binding', lambda: {'test_gpu': True})
    atomic_json(paths, sparse.GPU_RECEIPT, {'runtime': sparse.gpu_binding(), 'files': sparse.verify_code(paths)})
    assert sparse.installed(paths)
    target=paths.root/sparse.DIRECTORY/'code'/entry['name'];target.write_bytes(b'x'*len(source))
    assert not sparse.installed(paths)  # Same-size changes invalidate the fast status too.
    with pytest.raises(ValueError): loader.exec_module(module)


@pytest.mark.parametrize('extra', [{'url':'https://untrusted.invalid/code'}, {'profile_name':'h3-convrot-int8'}, {'name':'../code.py'}, {'revision':'0'*40}])
def test_sparse_transport_rejects_unbound_requests(extra):
    import time
    from h3server._download_transport import _fetch
    from h3server.backends.sparse import REVISION
    request = dict(component='sparse', name='source.zip', revision=REVISION, offset=0, timeout=1, deadline=time.monotonic()+10)
    with pytest.raises(ValueError): _fetch({**request, **extra})


def test_sparse_preferences_migrate_and_preserve_other_choices(tmp_path):
    from h3server.controller.preferences import Preferences
    prefs=Preferences(tmp_path/'private')
    prefs.settings(False,True,True,False)
    before=prefs.public()
    assert before['sparse_preset']=='off' and not before['install_sparse']
    prefs.settings(False,True,True,False,True,'quality')
    result=prefs.public()
    assert result['use_sage'] and result['install_turbo'] and result['sparse_preset']=='quality'
    prefs.settings(False,True)
    assert prefs.public()['sparse_preset']=='quality'
