import pytest
from fastapi.testclient import TestClient
from h3server.domain import Capabilities, JobRequest, GenerationSettings, JobStatus
from h3server.store import Store, OutputMetadata
from test_remote_api import service, HEADERS


def accept(store):
    return store.accept_job(JobRequest('TEST ONLY unchanged prompt', settings=GenerationSettings()), capabilities=Capabilities())


def test_favorite_persists_through_lifecycle_restart_and_unfavorite(tmp_path):
    store = Store(tmp_path/'jobs.sqlite')
    first = accept(store); second = accept(store)
    original = store.get_job(first.job_id)
    assert original.favorite is False
    marked = store.set_job_favorite(first.job_id, True)
    assert marked.favorite and marked.request == original.request and marked.timing == original.timing
    assert store.set_job_favorite(first.job_id, True) == marked
    store.transition_job(first.job_id, JobStatus.RUNNING)
    store.transition_job(first.job_id, JobStatus.COMPLETED, output=OutputMetadata('result.mp4', 4, 'video/mp4'))
    restored = Store(tmp_path/'jobs.sqlite')
    assert restored.get_job(first.job_id).favorite is True
    assert [j.job_id for j in restored.list_jobs()] == [second.job_id, first.job_id]
    assert restored.get_job_output(first.job_id).filename == 'result.mp4'
    assert not restored.set_job_favorite(first.job_id, False).favorite
    assert not Store(tmp_path/'jobs.sqlite').get_job(first.job_id).favorite
    restored.set_job_favorite(first.job_id, True)
    restored.delete_job(first.job_id, lambda _:None)
    with pytest.raises(KeyError): restored.set_job_favorite(first.job_id, True)


@pytest.mark.parametrize('value', [1,0,'true',None,[],{}])
def test_store_rejects_nonboolean_favorite(tmp_path,value):
    store=Store(tmp_path/'jobs.sqlite');job=accept(store)
    with pytest.raises(ValueError):store.set_job_favorite(job.job_id,value)
    assert store.get_job(job.job_id).favorite is False


def test_favorite_and_lifecycle_writes_do_not_lose_metadata(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store=Store(tmp_path/'jobs.sqlite');job=accept(store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        favorite=pool.submit(store.set_job_favorite,job.job_id,True)
        lifecycle=pool.submit(store.transition_job,job.job_id,JobStatus.RUNNING)
        favorite.result();lifecycle.result()
    result=store.get_job(job.job_id)
    assert result.favorite and result.status is JobStatus.RUNNING
    assert result.request==job.request


def test_authenticated_strict_favorite_endpoint(tmp_path):
    store, _, app=service(tmp_path);job=accept(store);path=f'/api/v1/jobs/{job.job_id}/favorite'
    with TestClient(app) as client:
        assert client.post(path,json={'favorite':True}).status_code==401
        for body in ({},{'favorite':1},{'favorite':'true'},{'favorite':True,'extra':1}):
            assert client.post(path,headers=HEADERS,json=body).status_code==422
        assert client.post('/api/v1/jobs/'+'a'*32+'/favorite',headers=HEADERS,json={'favorite':True}).status_code==404
        assert client.post(path,headers=HEADERS,json={'favorite':True}).json()=={'job_id':job.job_id,'favorite':True}
        assert client.get(f'/api/v1/jobs/{job.job_id}',headers=HEADERS).json()['favorite'] is True
        assert client.get('/api/v1/jobs',headers=HEADERS).json()['jobs'][0]['favorite'] is True
        assert client.post(path,headers=HEADERS,json={'favorite':False}).json()['favorite'] is False
