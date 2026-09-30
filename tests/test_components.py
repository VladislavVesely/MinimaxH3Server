import hashlib
import os
from dataclasses import replace
from types import SimpleNamespace
import pytest
from h3server.backends.base import atomic_json, regular_file, read_json
from h3server.backends.catalog import get_artifact_set
from h3server.backends import load_manifest
from h3server.downloads import record_consent

A='h3-convrot-int8'
B='h3-convrot-mixed'

@pytest.fixture
def components(tmp_path,monkeypatch):
    from test_remote_api import service
    from h3server.remote.worker import Backend
    from h3server.remote import components as module
    import h3server.remote.model_library as library_module
    import h3server.backends.pruned as pruned
    base=Backend(profiles=(A,),resident=True,readiness=lambda:(),command=('fixture',A))
    store,worker,app=service(tmp_path,backend=base)
    sync=app.state.components
    files=lambda name:{f'models/{name}/weight.bin':(3,hashlib.sha256(b'abc').hexdigest())}
    monkeypatch.setattr(module,'model_files',files)
    monkeypatch.setattr(pruned,'backend',lambda paths,name:replace(base,profiles=(name,),command=('fixture',name)))
    def put(name):
        record_consent(worker.paths,profile_name=name,artifact_set_digest=get_artifact_set(name).digest,
            license_sha256=load_manifest()['sources']['license']['sha256'],license_acknowledged=True,eligibility_confirmed=True,download_approved=True)
        with regular_file(worker.paths,next(iter(files(name))),flags=os.O_CREAT|os.O_WRONLY,mode='wb',create_parents=True) as f:f.write(b'abc')
    def verify(paths,*,profile_name,**kw):
        if not (paths.root/next(iter(files(profile_name)))).exists():raise FileNotFoundError()
    monkeypatch.setattr(library_module,'verify_snapshot',verify)
    monkeypatch.setattr(library_module,'download_model',lambda paths,**kw:put(kw['profile_name']))
    monkeypatch.setattr(library_module.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=b'{"environment_ready":true}'))
    monkeypatch.setattr(library_module.shutil,'disk_usage',lambda p:SimpleNamespace(free=10**13))
    put(A)
    return SimpleNamespace(sync=sync,worker=worker,store=store,app=app,put=put,files=files,module=module,root=tmp_path)

def apply(f,desired,**overrides):
    payload=dict(desired=desired,inventory_digest=f.sync.inventory()['digest'],license_sha256=load_manifest()['sources']['license']['sha256'],license_acknowledged=True,eligibility_confirmed=True,download_approved=True)
    payload.update(overrides)
    result=f.sync.start(**payload)
    f.sync.thread.join(10)
    assert not f.sync.thread.is_alive()
    return f.sync.status()

def test_batch_installs_before_removal_and_survives_restart(components):
    f=components
    sentinel=f.root/'outputs/keep.mp4';sentinel.parent.mkdir(exist_ok=True);sentinel.write_bytes(b'output')
    result=apply(f,[B])
    assert result['state']=='completed',result
    assert result['completed_actions']==2
    assert (f.root/next(iter(f.files(B)))).read_bytes()==b'abc'
    assert not (f.root/next(iter(f.files(A)))).exists()
    assert sentinel.read_bytes()==b'output'
    assert f.worker.backend.profiles==(B,)
    assert f.worker.backend.command_for_profile(B)==('fixture',B)
    from h3server.remote.model_library import ModelLibrary
    f.worker.backend=replace(f.worker.backend,profiles=(A,))
    ModelLibrary(f.worker)
    assert f.worker.backend.profiles==(B,)
    assert not f.worker.maintenance_status()['active']

def test_empty_selection_really_removes_base_and_does_not_resurrect(components):
    f=components
    assert apply(f,[])['state']=='completed'
    assert f.worker.backend.profiles==()
    assert f.worker.readiness()
    assert read_json(f.worker.paths,'config/model-library.json')=={'schema':2,'profiles':{}}

def test_failed_addition_does_not_remove_existing_model(components,monkeypatch):
    import h3server.remote.model_library as m
    monkeypatch.setattr(m,'download_model',lambda *a,**k:(_ for _ in ()).throw(ValueError('fixture download failure')))
    f=components;state=apply(f,[B])
    assert state['state']=='failed' and 'fixture download failure' in state['error']
    assert (f.root/next(iter(f.files(A)))).exists()
    assert not f.worker.maintenance_status()['active']

def test_stale_inventory_and_unknown_selection_refused(components):
    f=components
    with pytest.raises(ValueError,match='inventory'):apply(f,[],inventory_digest='0'*64)
    with pytest.raises(ValueError,match='Unknown'):apply(f,['../../outputs'])
    assert (f.root/next(iter(f.files(A)))).exists()

def test_jobs_or_another_maintenance_owner_block_changes(components):
    f=components;f.worker.begin_maintenance('1'*32,policy='reject')
    with pytest.raises(ValueError):apply(f,[])
    assert f.worker.maintenance_status()['operation']=='1'*32
    assert (f.root/next(iter(f.files(A)))).exists()
    f.worker.end_maintenance('1'*32)
    f.worker._active=object()
    with pytest.raises(ValueError,match='busy'):apply(f,[])
    f.worker._active=None

@pytest.mark.parametrize('kind',['symlink','hardlink'])
def test_hostile_managed_file_is_not_deleted(components,kind):
    f=components;path=f.root/next(iter(f.files(A)));outside=f.root/'untouched';outside.write_bytes(b'abc');path.unlink()
    if kind=='symlink':path.symlink_to(outside)
    else:os.link(outside,path)
    with pytest.raises((ValueError,OSError)):f.sync.inventory()
    assert outside.read_bytes()==b'abc' and path.exists()

def test_unmanaged_siblings_survive_removal(components):
    f=components;extra=(f.root/next(iter(f.files(A)))).parent/'my-notes.txt';extra.write_text('keep')
    assert apply(f,[])['state']=='completed'
    assert extra.read_text()=='keep'

def test_info_and_polling_do_not_mutate_and_request_contract_is_strict(components):
    from fastapi.testclient import TestClient
    from test_remote_api import HEADERS
    f=components
    with TestClient(f.app) as client:
        before=(f.root/next(iter(f.files(A)))).read_bytes()
        assert client.get('/api/v1/models/library').status_code==401
        data=client.get('/api/v1/models/library',headers=HEADERS).json()
        assert data['components']['schema']==1
        assert client.post('/api/v1/models/install',headers=HEADERS,json={'action':'synchronize','desired':[]}).status_code==422
        assert (f.root/next(iter(f.files(A)))).read_bytes()==before
