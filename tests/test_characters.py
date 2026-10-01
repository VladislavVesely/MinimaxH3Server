import pytest
from h3server.paths import AppPaths
from h3server.assets import AssetStore
from test_remote_api import service, HEADERS
from test_job_delete import job
from fastapi.testclient import TestClient


def library(tmp_path, monkeypatch):
    from h3server.characters import CharacterStore
    paths=AppPaths(tmp_path)
    for p in paths.directories(): p.mkdir(parents=True,exist_ok=True)
    monkeypatch.setattr('h3server.assets.probe',lambda fd:dict(kind='image',mime='image/png',width=1,height=1,duration=0,frames=1))
    image=AssetStore(paths).put(b'image fixture','image')['id']
    return CharacterStore(paths), dict(name='Ada',description='Dark hair',voice_asset_id=None,voice_description='',project_id=None,variants=[dict(id='a'*32,name='Blue outfit',description='Blue coat',image_asset_id=image)])


def test_persistence_revision_delete(tmp_path,monkeypatch):
    from h3server.characters import CharacterStore, RevisionConflict
    store,data=library(tmp_path,monkeypatch)
    created=store.create(data)
    assert created['revision']==1 and created['project_id'] is None
    other=CharacterStore(store.paths)
    assert other.list()==[created]
    changed=other.update(created['id'],{**data,'name':'Ada updated'},1)
    assert changed['revision']==2
    with pytest.raises(RevisionConflict):store.update(created['id'],data,1)
    with pytest.raises(RevisionConflict):store.delete(created['id'],1)
    store.delete(created['id'],2)
    assert other.list()==[]
    assert AssetStore(store.paths).metadata(data['variants'][0]['image_asset_id'])


@pytest.mark.parametrize('change',[{'name':' '},{'project_id':'project'},{'variants':[]},{'voice_asset_id':'a'*64},{'unknown':True}])
def test_invalid_records(tmp_path,monkeypatch,change):
    store,data=library(tmp_path,monkeypatch)
    with pytest.raises((ValueError,OSError)):store.create({**data,**change})
    assert store.list()==[]


def test_library_protects_job_assets(tmp_path,monkeypatch):
    store,data=library(tmp_path,monkeypatch);record=store.create(data)
    jobs,worker,_=service(tmp_path,min_free_bytes=0)
    asset=data['variants'][0]['image_asset_id'];j=job(jobs,asset=asset);worker.cancel(j.job_id)
    assert worker.delete(j.job_id)['removed_assets']==[]
    assert AssetStore(worker.paths).metadata(asset)
    store.delete(record['id'],1)
    j=job(jobs,asset=asset);worker.cancel(j.job_id)
    assert worker.delete(j.job_id)['removed_assets']==[asset]


def test_api_crud_auth_and_conflict(tmp_path,monkeypatch):
    _,data=library(tmp_path,monkeypatch);_,_,app=service(tmp_path)
    with TestClient(app) as c:
        assert c.get('/api/v1/characters').status_code==401
        assert c.post('/api/v1/characters',json=data).status_code==401
        r=c.post('/api/v1/characters',json=data,headers=HEADERS);assert r.status_code==201,r.text
        record=r.json();url='/api/v1/characters/'+record['id']
        assert c.get('/api/v1/characters',headers=HEADERS).json()['characters']==[record]
        assert c.get(url,headers=HEADERS).json()==record
        assert c.post(url+'/update',json={**data,'revision':1},headers=HEADERS).json()['revision']==2
        assert c.post(url+'/delete',json={'revision':1},headers=HEADERS).status_code==409
        assert c.post(url+'/delete',json={'revision':2},headers=HEADERS).status_code==200
        assert c.get(url,headers=HEADERS).status_code==404
        assert c.post('/api/v1/characters',json={**data,'project_id':'x'},headers=HEADERS).status_code==422
