import json
from fastapi.testclient import TestClient
from fastapi.responses import StreamingResponse
from h3server.controller.app import API,create_app
from h3server.controller.proxy import RemoteProxy


def test_favorite_route_enforces_auth_csrf_boolean_id_and_exact_forwarding(tmp_path,monkeypatch):
    calls=[]
    def request(self,method,path,**kwargs):
        calls.append((method,path,kwargs))
        return StreamingResponse(iter([json.dumps({'job_id':'a'*32,'favorite':True}).encode()]),media_type='application/json')
    monkeypatch.setattr(RemoteProxy,'request',request)
    origin='http://127.0.0.1:8787';app=create_app(state_dir=tmp_path,origin=origin)
    route=API+'/remote/jobs/'+'a'*32+'/favorite'
    with TestClient(app,base_url=origin) as client:
        assert client.post(route,headers={'origin':origin},json={'favorite':True}).status_code==401
        token=client.post(API+'/bootstrap',headers={'origin':origin},json={'token':app.state.session.bootstrap_token}).json()['csrf_token']
        assert client.post(route,headers={'origin':origin},json={'favorite':True}).status_code==403
        headers={'origin':origin,'x-csrf-token':token}
        assert client.post(route,headers=headers,json={'favorite':1}).status_code==422
        assert client.post(route,headers=headers,json={'favorite':True,'extra':True}).status_code==422
        assert client.post(API+'/remote/jobs/invalid/favorite',headers=headers,json={'favorite':True}).status_code==422
        assert calls==[]
        assert client.post(route,headers=headers,json={'favorite':True}).status_code==200
        assert calls==[('POST','/api/v1/jobs/'+'a'*32+'/favorite',{'body':{'favorite':True}})]
