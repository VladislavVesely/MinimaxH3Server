from dataclasses import replace
import os
import time
import pytest
from h3server.domain import GenerationSettings, JobRequest, JobStatus
from test_remote_api import service
from test_worker import fixture_backend, wait_status
from test_pruned_runtime import runtime, request


def test_component_cache_reuses_models_for_changed_geometry_and_steps(runtime,tmp_path):
    from h3server.backends.resident import ComponentCache
    from h3server.backends import pruned
    rt,calls=runtime;cache=ComponentCache()
    first=request()
    pruned.generate(first,tmp_path/'one.mp4',rt,emit=lambda e:None,cache=cache)
    second=replace(first,settings=replace(first.settings,steps=21,width=512,height=288))
    pruned.generate(second,tmp_path/'two.mp4',rt,emit=lambda e:None,cache=cache)
    names=[x[0] for x in calls]
    assert names.count('gguf')==1 and names.count('clip')==1 and names.count('vae')==2
    assert names.count('shift')==1 and names.count('condition')==2 and names.count('sample')==2


def test_sparse_request_state_is_fresh_while_weights_are_retained(runtime,tmp_path,monkeypatch):
    from types import SimpleNamespace as NS
    from h3server.backends.resident import ComponentCache
    from h3server.backends import pruned,sparse
    rt,calls=runtime;cache=ComponentCache();plans=[]
    rt.sparse=object();rt.actual_attention={}
    rt.shift=NS(execute=lambda model,*args:(model,))
    def apply(*args):
        model=NS(unpatch_model=lambda **kw:calls.append(('unpatch_objects',(),kw)));plans.append(model);return model
    monkeypatch.setattr(sparse,'apply',apply)
    monkeypatch.setattr(sparse,'trace_execution',lambda:{'native_sparse_calls':1})
    req=request('h3-convrot-int8');req=replace(req,settings=replace(req.settings,sparse_preset='quality'))
    for n in range(2):pruned.generate(req,tmp_path/f'{n}.mp4',rt,emit=lambda e:None,cache=cache)
    assert len(plans)==2
    assert ('unpatch_objects',(),{'unpatch_weights':False}) in calls
    assert [c[0] for c in calls].count('native')==1
    assert [c[0] for c in calls].count('clip')==1


def test_decoder_eviction_is_memory_conditional_and_preserves_base_encoder():
    from types import SimpleNamespace as NS
    from h3server.backends.resident import ComponentCache
    gib=1024**3
    for pressured in (False,True):
        cache=ComponentCache();cache.was_warm=True
        base=object();clip=NS(patcher=NS(model_size=lambda:13*gib))
        cache.components={'diffusion_model':base,'text_encoder':clip,'video_vae':object(),'audio_vae':object()}
        free=iter([gib,16*gib]) if pressured else iter([24*gib])
        mm=NS(get_free_memory=lambda d:next(free),get_torch_device=lambda:0,
            psutil=NS(virtual_memory=lambda:NS(available=8*gib),swap_memory=lambda:NS(free=4*gib)),cleanup_models=lambda:None,soft_empty_cache=lambda:None)
        events=[];cache.prepare_encoding(NS(mm=mm),events.append)
        assert cache.components['diffusion_model'] is base and cache.components['text_encoder'] is clip
        assert ('video_vae' in cache.components) is not pressured
        assert cache.reloaded_for_memory is pressured
        assert bool(events) is pressured


def test_tight_memory_keeps_encoder_instead_of_failing_or_oom():
    from types import SimpleNamespace as NS
    from h3server.backends.resident import ComponentCache
    gib=1024**3;cache=ComponentCache();cache.was_warm=True
    clip=NS(patcher=NS(model_size=lambda:13*gib))
    cache.components={'diffusion_model':object(),'text_encoder':clip,'video_vae':object(),'audio_vae':object()}
    cache.prepared=NS(unpatch_model=lambda **kw:None)
    free=iter([gib,2*gib,24*gib])
    mm=NS(get_free_memory=lambda d:next(free),get_torch_device=lambda:0,
        psutil=NS(virtual_memory=lambda:NS(available=4*gib),swap_memory=lambda:NS(free=2*gib)),cleanup_models=lambda:None,soft_empty_cache=lambda:None)
    cache.prepare_encoding(NS(mm=mm),lambda e:None)
    assert cache.components=={'text_encoder':clip} and cache.reloaded_base
    assert cache.prepared is None


def test_changed_turbo_releases_anon_base_only_on_constrained_hosts():
    from types import SimpleNamespace as NS
    from h3server.backends.resident import ComponentCache
    gib=1024**3
    for available in (16*gib,80*gib):
        cache=ComponentCache();cache.was_warm=True
        base=NS(model_size=lambda:20*gib);clip=NS(patcher=NS(model_size=lambda:13*gib))
        cache.components={'diffusion_model':base,'text_encoder':clip}
        mm=NS(get_free_memory=lambda d:24*gib,get_torch_device=lambda:0,
            psutil=NS(virtual_memory=lambda:NS(available=available)),cleanup_models=lambda:None,soft_empty_cache=lambda:None)
        cache.prepare_encoding(NS(mm=mm),lambda e:None,rebuild_turbo=True)
        assert cache.reloaded_base is (available==16*gib)
        assert cache.components['text_encoder'] is clip
        assert ('diffusion_model' in cache.components) is (available==80*gib)


def test_cached_file_changes_fail_closed(tmp_path):
    from h3server.backends.resident import ComponentCache
    from h3server.paths import AppPaths
    paths=AppPaths(tmp_path);p=tmp_path/'weights';p.write_bytes(b'123');p.chmod(0o600)
    cache=ComponentCache();cache.capture(paths,[p]);cache.verify(paths)
    p.write_bytes(b'456')
    with pytest.raises(ValueError,match='changed'):cache.verify(paths)


def test_authenticated_unload_and_estimate_api(tmp_path):
    from fastapi.testclient import TestClient
    from test_remote_api import HEADERS
    store,worker,app=service(tmp_path,backend=resident_backend(tmp_path),min_free_bytes=0)
    with TestClient(app) as client:
        assert client.post('/api/v1/models/unload',json={}).status_code==401
        assert client.post('/api/v1/models/unload',json={},headers=HEADERS).json()['loaded'] is False
        assert client.post('/api/v1/models/unload',json={'force':True},headers=HEADERS).status_code==422
        result=client.post('/api/v1/performance/estimate',json={'prompt':'not stored'},headers=HEADERS)
        assert result.status_code==200 and result.json()['samples']==0
        assert client.get('/api/v1/performance',headers=HEADERS).json()['records']==[]
        worker.submit(JobRequest('fixture',settings=GenerationSettings()))
        assert client.post('/api/v1/models/unload',json={},headers=HEADERS).status_code==409


def resident_backend(tmp_path, *, mismatch=False, fail=False):
    script = '''import json, pathlib, sys
for line in sys.stdin:
 command=json.loads(line)
 request=json.loads(pathlib.Path(command['request_path']).read_text())
 pathlib.Path(request['output_path']).write_bytes(b'fixture-mp4')
 print(json.dumps({'type':'result','job_id':JOB,'ok':OK,'components':['diffusion_model','text_encoder','video_vae','audio_vae'],'measurement':None}),flush=True)
'''.replace('JOB', "'0'*32" if mismatch else "command['job_id']").replace('OK', 'False' if fail else 'True')
    return replace(fixture_backend(tmp_path, script), resident=True)


def test_two_jobs_reuse_process_and_explicit_idle_unload(tmp_path):
    store, worker, _ = service(tmp_path, backend=resident_backend(tmp_path), min_free_bytes=0)
    worker.start()
    try:
        for _ in range(2):
            job=worker.submit(JobRequest('fixture',settings=GenerationSettings()))
            assert wait_status(store,job.job_id,{JobStatus.COMPLETED,JobStatus.FAILED}).status == JobStatus.COMPLETED
            with worker._lock:
                process=worker._resident_process
                assert worker.models_status()['loaded']
            if _==0: original=process
            else: assert process is original and process.poll() is None
        for _ in range(100):
            if worker.models_status()['can_unload']: break
            time.sleep(.01)
        assert worker.unload_models()['loaded'] is False
        assert original.poll() is not None
        assert not worker.unload_models()['loaded']
    finally:worker.stop()


def test_profile_change_replaces_retained_process(tmp_path):
    from h3server.domain import H3_PROFILES
    store,worker,_=service(tmp_path,backend=resident_backend(tmp_path),min_free_bytes=0)
    worker.start()
    try:
        previous=None
        for profile in H3_PROFILES:
            job=worker.submit(JobRequest('fixture',settings=GenerationSettings(profile=profile)))
            assert wait_status(store,job.job_id,{JobStatus.COMPLETED,JobStatus.FAILED}).status==JobStatus.COMPLETED
            current=worker._resident_process
            if previous is not None:assert previous is not current and previous.returncode is not None
            previous=current
    finally:worker.stop()


def test_stop_never_removes_completed_video(tmp_path):
    from h3server.store import OutputMetadata
    store,worker,_=service(tmp_path,backend=resident_backend(tmp_path),min_free_bytes=0)
    job=worker.submit(JobRequest('fixture',settings=GenerationSettings()))
    store.claim_next_job();worker._prepare(job)
    video=worker.paths.job_dir(job.job_id)/'video.mp4';video.write_bytes(b'fixture-mp4')
    store.transition_job(job.job_id,JobStatus.COMPLETED,output=OutputMetadata('video.mp4',11,'video/mp4'))
    worker._active=job.job_id
    worker.stop()
    assert video.exists()


def test_statistics_failure_does_not_block_estimate_or_generation(tmp_path,monkeypatch):
    store,worker,_=service(tmp_path,backend=resident_backend(tmp_path),min_free_bytes=0)
    worker._hardware={'cpu_model':'fixture'}
    def broken(*args):raise ValueError('broken averages')
    monkeypatch.setattr(worker.performance,'estimate',broken)
    request=JobRequest('fixture',settings=GenerationSettings())
    assert worker.estimate(request)['samples']==0
    worker.start()
    try:
        job=worker.submit(request)
        assert wait_status(store,job.job_id,{JobStatus.COMPLETED,JobStatus.FAILED}).status==JobStatus.COMPLETED
    finally:worker.stop()


def test_unload_refuses_queued_work(tmp_path):
    store, worker, _=service(tmp_path,backend=resident_backend(tmp_path),min_free_bytes=0)
    worker.submit(JobRequest('fixture',settings=GenerationSettings()))
    with pytest.raises(ValueError,match='busy'):worker.unload_models()


@pytest.mark.parametrize('options',[{'mismatch':True},{'fail':True}])
def test_bad_completion_recycles_worker(tmp_path,options):
    store,worker,_=service(tmp_path,backend=resident_backend(tmp_path,**options),min_free_bytes=0)
    worker.start()
    try:
        job=worker.submit(JobRequest('fixture',settings=GenerationSettings()))
        assert wait_status(store,job.job_id,{JobStatus.FAILED,JobStatus.COMPLETED}).status == JobStatus.FAILED
        for _ in range(100):
            if not worker.models_status()['loaded']:break
            time.sleep(.01)
        assert not worker.models_status()['loaded']
    finally:worker.stop()
