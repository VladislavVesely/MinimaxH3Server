import sys
from pathlib import Path
from h3server.paths import AppPaths
from h3server.backends.base import atomic_json
from h3server.backends import sage_attention as sage


def test_sage_receipts_are_environment_local(tmp_path,monkeypatch):
    paths=AppPaths(tmp_path)
    a=paths.environments/('a'*64);b=paths.environments/('b'*64)
    for p in (a,b):
        p.mkdir(parents=True);(p/'resolved.txt').write_text('torch==fixture\n')
    monkeypatch.setattr(sys,'prefix',str(a))
    monkeypatch.setattr(sage.metadata,'version',lambda name:sage.VERSION if name=='sageattention' else 'fixture')
    first={'version':sage.VERSION,'wheel_sha256':sage.WHEEL_SHA256,'environment':str(a),'torch':'fixture','gpu_probe':True}
    atomic_json(paths,sage.RECEIPT,first)
    assert sage.installed(paths)
    assert not sage.installed(paths,environment=b)
    atomic_json(paths,sage.receipt_path(paths,environment=b),{**first,'environment':str(b)})
    assert sage.installed(paths,environment=b)
    assert sage.installed(paths)
    assert sage.receipt_path(paths,environment=a)!=sage.receipt_path(paths,environment=b)
