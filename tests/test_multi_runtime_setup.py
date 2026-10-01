from contextlib import contextmanager
from types import SimpleNamespace
import threading
from h3server.controller.setup import SetupCoordinator
from h3server.controller.deploy import Deployment
from h3server.installer.planner import ReleaseArtifact
from test_controller_security import controller, bootstrap, connect


def test_bootstrap_reconciles_the_entire_desired_set_once():
    coordinator=object.__new__(SetupCoordinator)
    desired=['h3-convrot-int8','h3-gguf-q5-k-m','sageattention','reference']
    coordinator._desired=desired;coordinator._component_operation=None
    coordinator._call=lambda fn,**kw:fn()
    coordinator._publish=lambda **kw:None;coordinator._wait=lambda:None
    sent=[];reads=[]
    def request(method,body=None):
        if method=='POST':
            sent.append(body)
            return {'busy':True,'operation':'a'*32,'state':'running'}
        reads.append(method)
        return {'components':{'supported':True,'digest':'b'*64},'synchronization':{'busy':False,'state':'completed','operation':'a'*32}}
    coordinator.component_request=request
    coordinator._complete_components()
    assert len(sent)==1 and sent[0]['desired']==desired
    assert sent[0]['inventory_digest']=='b'*64
    assert all(sent[0][key] is True for key in ['license_acknowledged','eligibility_confirmed','download_approved'])
    assert coordinator._component_operation=='a'*32


def test_staging_runtime_wheel_does_not_replace_service_or_setup_pointers(monkeypatch):
    import h3server.controller.deploy as module
    writes=[]
    @contextmanager
    def transaction(root,**kwargs):
        assert kwargs=={'discovery':True};yield lambda:None
    @contextmanager
    def sftp():yield object()
    connection=SimpleNamespace(lock=threading.RLock(),staging_transaction=transaction,private_sftp=sftp)
    artifact=ReleaseArtifact('fixture','h3server-0.1-py3-none-any.whl','a'*64)
    deployment=Deployment(connection,SimpleNamespace(artifact=artifact,data=b'wheel'))
    deployment.root='/owned/MinimaxH3Server';deployment.plan={'keep':'existing plan'}
    monkeypatch.setattr(deployment,'_authenticated',lambda:None)
    monkeypatch.setattr(module,'_write',lambda client,path,data,**kwargs:writes.append((path,data,kwargs)))
    assert deployment.stage_component_release()['sha256']=='a'*64
    assert writes==[('/owned/MinimaxH3Server/releases/fixture/h3server-0.1-py3-none-any.whl',b'wheel',{'immutable':True})]
    assert deployment.plan=={'keep':'existing plan'}


def test_controller_stages_current_wheel_only_on_explicit_apply(controller,monkeypatch):
    from h3server.controller.proxy import RemoteProxy
    from fastapi.responses import JSONResponse
    app,client,_=controller;headers=bootstrap(app,client);staged=[];forwarded=[]
    fingerprint=connect(client,headers).json()['fingerprint']
    assert client.post('/api/v1/controller/approve',headers=headers,json={'fingerprint':fingerprint}).status_code==200
    assert client.post('/api/v1/controller/authenticate',headers=headers,json={'password':'fixture'}).status_code==200
    artifact=dict(release_id='current',filename='h3server-0.1-py3-none-any.whl',sha256='a'*64)
    app.state.deployment.release=object()
    monkeypatch.setattr(app.state.deployment,'stage_component_release',lambda:staged.append(True) or artifact)
    def request(self,method,path,**kwargs):
        forwarded.append((method,path,kwargs));return JSONResponse({'ok':True})
    monkeypatch.setattr(RemoteProxy,'request',request)
    assert client.get('/api/v1/controller/remote/models/library',headers=headers).status_code==200
    assert not staged
    assert client.post('/api/v1/controller/remote/models/install',headers=headers,json={
        'action':'synchronize','desired':['h3-gguf-q5-k-m'],'inventory_digest':'b'*64,
        'license_sha256':'c'*64,'license_acknowledged':True,'eligibility_confirmed':True,'download_approved':True,
        'runtime_artifact':{**artifact,'release_id':'unapproved'}}).status_code==200
    assert staged==[True]
    assert forwarded[-1][2]['body']['runtime_artifact']==artifact
