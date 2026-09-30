import pytest
from h3server.domain import GenerationSettings, JobRequest, JobStatus
from test_remote_api import service
from test_controller_security import controller


def job(store, *, asset=None):
    from h3server.domain import Capabilities
    settings=GenerationSettings(profile='h3-convrot-int8',input_mode='references',reference_images=(asset,) if asset else ())
    return store.accept_job(JobRequest('private prompt',settings=settings),capabilities=Capabilities())


def test_delete_requires_cancel_and_cleans_shared_assets(tmp_path):
    store,worker,_=service(tmp_path,min_free_bytes=0)
    asset='a'*64
    a=job(store,asset=asset);b=job(store,asset=asset)
    with pytest.raises(ValueError,match='cancel'):worker.delete(a.job_id)
    root=worker.paths.root/'assets';root.mkdir();(root/(asset+'.bin')).write_bytes(b'fixture');(root/(asset+'.json')).write_text('{}')
    output=worker.paths.job_dir(a.job_id);output.mkdir(parents=True);(output/'video.mp4').write_bytes(b'fixture');(output/'nested').mkdir();(output/'nested'/'temp').write_text('private')
    worker.cancel(a.job_id)
    worker._active=a.job_id
    with pytest.raises(ValueError,match='stopped'):worker.delete(a.job_id)
    worker._active=None
    assert worker.delete(a.job_id)['removed_assets']==[]
    assert not output.exists() and (root/(asset+'.bin')).exists()
    with pytest.raises(KeyError):store.get_job(a.job_id)
    assert all(e.subject_id!=a.job_id for e in store.read_events().events)
    from h3server.store import Store
    assert Store(store.path).get_job(b.job_id).job_id==b.job_id
    worker.cancel(b.job_id)
    assert worker.delete(b.job_id)['removed_assets']==[asset]
    assert not list(root.iterdir())
    assert worker.delete(b.job_id)['deleted']==b.job_id


def test_delete_does_not_follow_symlinks_and_can_retry(tmp_path):
    store,worker,_=service(tmp_path,min_free_bytes=0);a=job(store);worker.cancel(a.job_id)
    external=tmp_path/'external';external.mkdir();(external/'keep').write_text('keep')
    path=worker.paths.job_dir(a.job_id);path.parent.mkdir(parents=True,exist_ok=True);path.symlink_to(external,target_is_directory=True)
    with pytest.raises(OSError):worker.delete(a.job_id)
    assert store.get_job(a.job_id) and (external/'keep').exists()
    path.unlink();path.mkdir();(path/'link').symlink_to(external,target_is_directory=True)
    worker.delete(a.job_id)
    assert (external/'keep').read_text()=='keep'


def test_delete_api_auth_cancel_first_and_idempotence(tmp_path):
    from fastapi.testclient import TestClient
    from test_remote_api import HEADERS
    store,worker,app=service(tmp_path,min_free_bytes=0);a=job(store)
    client=TestClient(app);url='/api/v1/jobs/'+a.job_id+'/delete'
    assert client.post(url,json={}).status_code==401
    assert client.post(url,json={},headers=HEADERS).status_code==409
    assert store.get_job(a.job_id).status==JobStatus.QUEUED
    assert client.post('/api/v1/jobs/'+a.job_id+'/cancel',headers=HEADERS).status_code==200
    assert client.post(url,json={},headers=HEADERS).status_code==200
    assert client.post(url,json={},headers=HEADERS).status_code==200
    assert client.get('/api/v1/jobs/'+a.job_id,headers=HEADERS).status_code==404
    assert client.get('/api/v1/events?follow=false',headers=HEADERS).status_code==200


def test_delete_rebuilds_timing_averages(tmp_path):
    from h3server.performance import configuration
    store,worker,_=service(tmp_path,min_free_bytes=0)
    a=job(store);b=job(store);config=configuration(a.request,worker.paths)
    for j,total in [(a,10),(b,20)]:
        worker.performance.record(j.job_id,config,{},dict(total_seconds=total,stages={'loading':total},sampling_steps=[total]),'cold',{'sparse':'off','sage':False})
    worker.cancel(a.job_id);worker.delete(a.job_id)
    result=worker.performance.estimate(config,{},'cold')
    assert result['samples']==1 and result['mean_seconds']==20
    assert result['stages']['loading']['mean_seconds']==20
    assert result['sampling_steps'][0]['mean_seconds']==20
    worker.cancel(b.job_id);worker.delete(b.job_id)
    assert worker.performance.estimate(config,{},'cold')['samples']==0
    assert worker.performance.export()['records']==[]


def test_submission_validation_is_serialized_with_deletion(tmp_path):
    from concurrent.futures import ThreadPoolExecutor,TimeoutError
    from threading import Event
    from h3server.remote.worker import Backend
    entered=Event();release=Event()
    file=tmp_path/'assets'/('a'*64+'.bin');file.parent.mkdir();file.write_bytes(b'fixture')
    def validate(request):
        assert file.exists();entered.set();assert release.wait(3);assert file.exists()
    backend=Backend(command=('fixture-never-executed',),readiness=lambda:(),validate_request=validate)
    store,worker,_=service(tmp_path,backend=backend,min_free_bytes=0)
    a=job(store,asset='a'*64);worker.cancel(a.job_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending=pool.submit(worker.submit,a.request)
        assert entered.wait(2)
        deleting=pool.submit(worker.delete,a.job_id)
        try:
            with pytest.raises(TimeoutError):deleting.result(timeout=.05)
        finally:release.set()
        b=pending.result();assert deleting.result()['removed_assets']==[]
    assert file.exists() and store.get_job(b.job_id).status==JobStatus.QUEUED


def test_partial_cleanup_failure_keeps_retryable_record(tmp_path,monkeypatch):
    store,worker,_=service(tmp_path,min_free_bytes=0);a=job(store);worker.cancel(a.job_id)
    original=worker.performance.delete_job
    def fail(id):raise OSError('fixture cleanup failure')
    monkeypatch.setattr(worker.performance,'delete_job',fail)
    with pytest.raises(OSError):worker.delete(a.job_id)
    assert store.get_job(a.job_id)
    monkeypatch.setattr(worker.performance,'delete_job',original)
    assert worker.delete(a.job_id)['deleted']==a.job_id


def test_missing_file_in_other_draft_is_not_disk_full(tmp_path):
    from fastapi.testclient import TestClient
    from test_remote_api import HEADERS
    from h3server.remote.worker import Backend
    def missing(request):raise FileNotFoundError('fixture deleted reference')
    _,_,app=service(tmp_path,backend=Backend(command=('fixture-never-executed',),readiness=lambda:(),validate_request=missing))
    r=TestClient(app).post('/api/v1/jobs',json={'prompt':'fixture'},headers=HEADERS)
    assert r.status_code==422 and r.json()['detail']=='required_generation_file_missing'


def test_delete_controller_requires_csrf(controller,monkeypatch):
    from test_controller_security import bootstrap,API,ORIGIN
    from h3server.controller.proxy import RemoteProxy
    from fastapi.responses import JSONResponse
    app,client,_=controller;calls=[]
    def forward(self,method,path,**kwargs):
        calls.append((method,path,kwargs));return JSONResponse({'deleted':'a'*32,'removed_assets':[]})
    monkeypatch.setattr(RemoteProxy,'request',forward)
    url=API+'/remote/jobs/'+'a'*32+'/delete'
    assert client.post(url,json={},headers={'origin':ORIGIN}).status_code==401
    headers=bootstrap(app,client)
    assert client.post(url,json={},headers={'origin':ORIGIN}).status_code==403
    assert not calls
    assert client.post(url,json={},headers=headers).status_code==200
    assert calls==[('POST','/api/v1/jobs/'+'a'*32+'/delete',{'body':{}})]
