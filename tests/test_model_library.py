import hashlib
import pytest
from h3server.paths import AppPaths
from h3server.backends.base import regular_file


def test_verified_shared_copy_is_independent_and_rejects_corruption(tmp_path):
    from h3server.downloads import _reuse_shared
    paths=AppPaths(tmp_path);data=b'shared original bytes'
    entry={'rfilename':'model.bin','size':len(data),'lfs':{'sha256':hashlib.sha256(data).hexdigest()}}
    with regular_file(paths,'models/source.bin',flags=__import__('os').O_CREAT|__import__('os').O_RDWR,mode='w+b',create_parents=True) as f:f.write(data)
    with regular_file(paths,'tmp/copy.part',flags=__import__('os').O_CREAT|__import__('os').O_RDWR,mode='w+b',create_parents=True) as f:
        assert _reuse_shared(paths,entry,['models/source.bin'],f,lambda:False)
        f.seek(0);assert f.read()==data
        f.seek(0);f.write(b'changed')
    assert (tmp_path/'models/source.bin').read_bytes()==data
    assert (tmp_path/'models/source.bin').stat().st_nlink==1
    (tmp_path/'models/source.bin').write_bytes(b'x'*len(data))
    with (tmp_path/'tmp/copy.part').open('w+b') as f:assert not _reuse_shared(paths,entry,['models/source.bin'],f,lambda:False)


def test_completed_setup_is_new_profile_install_not_failed_stage_retry():
    from contextlib import contextmanager
    from types import SimpleNamespace
    from h3server.controller.deploy import Deployment
    calls=[]
    @contextmanager
    def staging(root,**kw):
        assert kw=={'discovery':True};calls.append('lock');yield lambda:None
    snapshot={'setups':[{'plan_digest':'d','state':'ready'}], 'active_release':{'environment':'d'},'operation':{'state':'completed'}}
    document={'plan':{'profile':'old'},'plan_digest':'d','snapshot':snapshot}
    fake=SimpleNamespace(root='/owned',compatible=True,observe_saved=lambda:document,
        connection=SimpleNamespace(staging_transaction=staging),make_plan=lambda name,**kw:calls.append(name) or {'new':name})
    assert Deployment.prepare_additional_setup(fake,'new')=={'new':'new'}
    assert calls==['lock','new'] and fake.compatible is False
    calls.clear()
    for state in ('running','interrupted','failed',None,'unknown'):
        snapshot['operation']['state']=state
        assert Deployment.prepare_additional_setup(fake,'new') is None
        assert calls==[]


@pytest.mark.parametrize('diagnostic_issue',[None,'fixture runtime issue'])
def test_add_model_registers_and_routes_both_models_without_replacing_base(tmp_path,monkeypatch,diagnostic_issue):
    import json
    from types import SimpleNamespace
    from test_remote_api import service,HEADERS
    from fastapi.testclient import TestClient
    from h3server.remote.worker import Backend
    from h3server.backends import load_manifest
    from h3server.backends.catalog import get_artifact_set
    import h3server.remote.model_library as module
    base=Backend(profiles=('h3-convrot-int8',),resident=True,readiness=lambda:(),command=('base-python','base-worker'))
    store,worker,app=service(tmp_path,backend=base)
    library=app.state.model_library;downloads=[]
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=bool(diagnostic_issue),stdout=json.dumps({'environment_ready':not diagnostic_issue,'issues':[diagnostic_issue] if diagnostic_issue else []}).encode()))
    monkeypatch.setattr(module,'verify_snapshot',lambda *a,**kw:(_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(module.shutil,'disk_usage',lambda _:SimpleNamespace(free=10**13))
    monkeypatch.setattr(module,'download_model',lambda paths,**kw:downloads.append(kw['profile_name']))
    body=dict(profile='h3-convrot-mixed',artifact_set_digest=get_artifact_set('h3-convrot-mixed').digest,
        license_sha256=load_manifest()['sources']['license']['sha256'],license_acknowledged=True,eligibility_confirmed=True,download_approved=True)
    with TestClient(app) as client:
        assert client.get('/api/v1/models/library').status_code==401
        assert client.post('/api/v1/models/install',headers=HEADERS,json={**body,'artifact_set_digest':'0'*64}).status_code==409
        assert client.post('/api/v1/models/install',headers=HEADERS,json=body).status_code==202
        library.thread.join(timeout=5);assert not library.thread.is_alive()
        if diagnostic_issue:
            assert library.status()['state']=='failed'
            assert diagnostic_issue in library.status()['error']
            assert worker.backend.profiles==base.profiles and downloads==[]
            assert not worker.maintenance_status()['active']
            return
        assert library.status()['state']=='installed',library.status()
        assert worker.backend.profiles==('h3-convrot-int8','h3-convrot-mixed')
        assert worker.backend.command_for_profile('h3-convrot-int8')==base.command
        assert worker.backend.command_for_profile('h3-convrot-mixed')[-1]=='h3-convrot-mixed'
        with pytest.raises(ValueError):worker.backend.command_for_profile('../other')
        assert downloads==['h3-convrot-mixed']
        assert not worker.maintenance_status()['active']
        assert not store.list_jobs()
        assert client.post('/api/v1/models/install/cancel',headers=HEADERS,json={'operation':'0'*32}).status_code==409
        registered=json.loads((tmp_path/library.registry).read_text())
        assert registered['profiles']['h3-convrot-mixed']==body['artifact_set_digest']


def test_shared_runtime_requires_target_consent_and_preserves_receipt(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    from h3server.paths import AppPaths
    from h3server.backends.base import atomic_json
    from h3server.backends import load_manifest
    from h3server.backends.catalog import get_artifact_set
    from h3server.downloads import record_consent
    import h3server.backends.comfy_runtime as runtime
    paths=AppPaths(tmp_path);env=paths.environments/('a'*64);env.mkdir(parents=True,mode=0o700)
    resolved=env/'resolved.txt';resolved.write_text('fixture==1\n');resolved.chmod(0o600)
    receipt=runtime.runtime_record('h3-convrot-int8','fixture==1\n')
    atomic_json(paths,'environments/'+env.name+'/runtime.json',receipt)
    before=(env/'runtime.json').read_bytes()
    monkeypatch.setattr(runtime.sys,'prefix',str(env))
    monkeypatch.setattr(runtime,'verify_sources',lambda *a:None)
    monkeypatch.setattr(runtime.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout=b'fixture==1\n'))
    with pytest.raises((FileNotFoundError,ValueError)):runtime.verify_provisioned(paths,'h3-convrot-mixed')
    record_consent(paths,profile_name='h3-convrot-mixed',artifact_set_digest=get_artifact_set('h3-convrot-mixed').digest,
        license_sha256=load_manifest()['sources']['license']['sha256'],license_acknowledged=True,eligibility_confirmed=True,download_approved=True)
    assert runtime.verify_provisioned(paths,'h3-convrot-mixed')==env
    assert (env/'runtime.json').read_bytes()==before
    with pytest.raises(ValueError):runtime.verify_provisioned(paths,'h3-gguf-q4-k-m')


def test_runtime_families_and_registry_binding():
    from h3server.backends.comfy_runtime import compatible_runtime
    assert compatible_runtime('h3-convrot-int8','h3-convrot-mixed')
    assert compatible_runtime('h3-gguf-q4-k-m','h3-gguf-q5-k-m')
    assert not compatible_runtime('h3-convrot-int8','h3-gguf-q4-k-m')
