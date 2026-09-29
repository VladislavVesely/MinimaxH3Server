import pytest
from h3server.domain import GenerationSettings
from h3server.remote.app import GenerationInput


def test_turbo_settings_are_explicit_and_preserved():
    request = GenerationInput(prompt='test', profile='h3-convrot-int8', turbo_lora=True,
                              turbo_strength=1.1, turbo_low_vram=True, steps=6).to_request()
    assert request.settings.turbo_lora is True
    assert request.settings.turbo_strength == 1.1
    assert request.settings.turbo_low_vram is True
    assert GenerationSettings().turbo_lora is False


@pytest.mark.parametrize('changes', [{'steps':3}, {'steps':20}, {'turbo_strength':float('nan')},
    {'turbo_strength':0}, {'profile':'h3-gguf-q4-k-m'}, {'turbo_lora':1}])
def test_unsupported_turbo_requests_fail(changes):
    values=dict(profile='h3-convrot-int8', steps=6, turbo_lora=True)
    values.update(changes)
    with pytest.raises(ValueError): GenerationSettings(**values)


@pytest.mark.parametrize('changes', [{'component':'other'}, {'revision':'0'*40}, {'url':'https://example.invalid'}, {'name':'../escape'}])
def test_component_transport_rejects_unpinned_requests(changes):
    import time
    from h3server._download_transport import _fetch
    from h3server.backends import turbo
    document=dict(component='turbo',name=turbo.WEIGHT,revision=turbo.REVISION,offset=0,timeout=30,deadline=time.monotonic()+60)
    document.update(changes)
    with pytest.raises(ValueError): _fetch(document)


def test_install_requires_exact_pin_before_io():
    from types import SimpleNamespace
    from h3server.remote.turbo import TurboInstaller
    installer=TurboInstaller(SimpleNamespace(paths=None))
    with pytest.raises(ValueError,match='selection changed'): installer.start('0'*64)


def test_turbo_strength_rejects_boolean():
    with pytest.raises(ValueError):
        GenerationInput(prompt='test',profile='h3-convrot-int8',turbo_lora=True,turbo_strength=True,steps=6)


def test_turbo_refuses_a_noop_loader():
    from types import SimpleNamespace
    from h3server.backends.turbo import apply
    model=SimpleNamespace(model=SimpleNamespace(diffusion_model=SimpleNamespace(use_adaln_curves=True)))
    unchanged=SimpleNamespace(injections={},patches={},object_patches={})
    module=SimpleNamespace(MiniMaxH3TurboLoRA=lambda:SimpleNamespace(apply_lora=lambda *a,**kw:(unchanged,)))
    settings=GenerationSettings(profile='h3-convrot-int8',steps=6,turbo_lora=True)
    with pytest.raises(ValueError,match='backbone'):apply(model,settings,module)


def test_optional_download_resumes_and_verifies(tmp_path, monkeypatch):
    import hashlib, io, os
    from h3server.paths import AppPaths
    from h3server.backends import turbo
    from h3server.backends.base import atomic_json, regular_file
    from h3server import _download_transport
    payload=b'pinned test bytes'
    entry=dict(name='test.safetensors',size=len(payload),sha256=hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(turbo,'FILES',[entry]);monkeypatch.setattr(turbo,'TOTAL',len(payload))
    paths=AppPaths(tmp_path/'root');paths.root.mkdir(mode=0o700)
    atomic_json(paths,turbo.CONSENT,turbo.BINDING)
    partial='tmp/turbo-'+turbo.DIGEST+'/test.safetensors.part'
    with regular_file(paths,partial,flags=os.O_CREAT|os.O_WRONLY,mode='wb',create_parents=True) as f:f.write(payload[:3])
    offsets=[]
    class Transport:
        def __init__(self, paths, name, offset, **kwargs):
            offsets.append(offset);self.body=io.BytesIO(payload[offset:]);self.status=206 if offset else 200
            self.headers={'Content-Length':str(len(payload)-offset),'Content-Range':f'bytes {offset}-{len(payload)-1}/{len(payload)}'}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,n):return self.body.read(n)
    monkeypatch.setattr(_download_transport,'DownloadTransport',Transport)
    progress=[];turbo.download(paths,progress=lambda n,total:progress.append((n,total)))
    assert offsets==[3]
    assert progress[-1]==(len(payload),len(payload))
    assert turbo.installed(paths,verify=True)
    target=paths.root/turbo.DIRECTORY/entry['name'];target.write_bytes(b'x'*len(payload))
    assert not turbo.installed(paths,verify=True)
    turbo.download(paths,progress=lambda *args:None)
    assert offsets==[3,0]
    assert target.read_bytes()==payload
