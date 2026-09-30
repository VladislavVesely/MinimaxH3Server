import base64, os
import pytest
from test_controller_security import controller
from h3server.paths import AppPaths

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jGZkAAAAASUVORK5CYII=')

def test_store_rejects_invalid_ids_and_empty_upload(tmp_path):
    from h3server.assets import AssetStore
    store=AssetStore(AppPaths(tmp_path))
    for id in ('../escape','A'*64,'a'*63):
        with pytest.raises(ValueError):store.metadata(id)
    with pytest.raises(ValueError):store.put(b'', 'image')

def test_upload_is_content_addressed_private_and_immutable(tmp_path,monkeypatch):
    from h3server.assets import AssetStore
    import h3server.assets as assets
    monkeypatch.setattr(assets,'probe',lambda fd:dict(kind='image',mime='image/png',width=1,height=1,duration=0,frames=1))
    store=AssetStore(AppPaths(tmp_path))
    a=store.put(PNG,'image');b=store.put(PNG,'image')
    assert a==b and len(a['id'])==64
    with store.open(a['id']) as stream:
        assert stream.read()==PNG
        assert os.fstat(stream.fileno()).st_mode & 0o077==0
    with pytest.raises(ValueError):store.put(PNG,'audio')
    (tmp_path/'assets'/f"{a['id']}.bin").write_bytes(b'changed')
    with pytest.raises(ValueError):store.metadata(a['id'])

def test_symlink_asset_directory_rejected(tmp_path):
    from h3server.assets import AssetStore
    (tmp_path/'outside').mkdir();(tmp_path/'assets').symlink_to(tmp_path/'outside',target_is_directory=True)
    with pytest.raises((ValueError,OSError)):AssetStore(AppPaths(tmp_path)).put(PNG,'image')

def test_asset_api_authenticated_upload_and_range(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from test_remote_api import service,HEADERS
    import h3server.assets as assets
    monkeypatch.setattr(assets,'probe',lambda fd:dict(kind='image',mime='image/png',width=1,height=1,duration=0,frames=1))
    _,_,app=service(tmp_path)
    with TestClient(app) as c:
        assert c.post('/api/v1/assets/image',content=PNG).status_code==401
        r=c.post('/api/v1/assets/image',content=PNG,headers=HEADERS)
        assert r.status_code==201,r.text
        id=r.json()['id']
        assert c.get('/api/v1/assets/'+id,headers=HEADERS).json()['kind']=='image'
        r=c.get('/api/v1/assets/'+id+'/content',headers={**HEADERS,'Range':'bytes=0-7'})
        assert r.status_code==206 and r.content==PNG[:8]
        assert c.post('/api/v1/assets/audio',content=PNG,headers=HEADERS).status_code==422


def test_controller_asset_upload_keeps_csrf_and_bounded_binary(controller,monkeypatch):
    from test_controller_security import bootstrap,API,ORIGIN
    from h3server.controller.proxy import RemoteProxy
    from fastapi.responses import JSONResponse
    app,c,_=controller
    called=[]
    def forward(self,method,path,**kw):called.append((method,path,kw));return JSONResponse({'ok':True})
    monkeypatch.setattr(RemoteProxy,'request',forward)
    route=API+'/remote/assets/image'
    assert c.post(route,content=b'x',headers={'origin':ORIGIN}).status_code==401
    headers=bootstrap(app,c)
    assert c.get(API+'/remote/features/reference').status_code==200
    assert c.post(route,content=b'x',headers={'origin':ORIGIN}).status_code==403
    payload=b'x'*20000
    r=c.post(route,content=payload,headers={**headers,'content-type':'application/octet-stream'})
    assert r.status_code==200,r.text
    assert called[-1][2]['binary']==payload
    r=c.post(API+'/remote/jobs',json={'prompt':'界'*10000},headers=headers)
    assert r.status_code==200,r.text
    assert called[-1][2]['body']['prompt']=='界'*10000
    assert c.post(route,content=b'',headers={**headers,'content-length':str(64*1024*1024+1)}).status_code==413


def test_decode_probe_rejects_nonmedia(tmp_path):
    from h3server.assets import probe
    f=tmp_path/'bad';f.write_bytes(b'#EXTM3U\nhttp://127.0.0.1/secret')
    with f.open('rb') as s:
        with pytest.raises(ValueError):probe(s.fileno())
