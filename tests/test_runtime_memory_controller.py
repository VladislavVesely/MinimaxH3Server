from fastapi.testclient import TestClient
from fastapi.responses import StreamingResponse
import json
from h3server.controller.app import API, create_app
from h3server.controller.proxy import RemoteProxy

ORIGIN = 'http://127.0.0.1:8787'
SETTINGS = {'reserve_vram_gib':4.0,'offload_mode':'low_vram','expandable_segments':True}


def test_exact_controller_route_auth_csrf_validation_and_forwarding(tmp_path, monkeypatch):
    calls = []
    def request(self, method, path, **kwargs):
        calls.append((method,path,kwargs))
        return StreamingResponse(iter([json.dumps({'settings':SETTINGS,'revision':'a'*64,'supported':True,'busy':False}).encode()]),media_type='application/json')
    monkeypatch.setattr(RemoteProxy, 'request', request)
    app = create_app(state_dir=tmp_path, origin=ORIGIN)
    route = API + '/remote/settings/memory'
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get(route).status_code == 401
        bootstrap = client.post(API+'/bootstrap',headers={'origin':ORIGIN},json={'token':app.state.session.bootstrap_token}).json()
        response = client.get(route)
        assert response.status_code == 200
        assert len(response.json()['target_key']) == 64
        assert calls[-1][0:2] == ('GET','/api/v1/settings/memory')
        body = {'settings':SETTINGS,'revision':'a'*64,'target_key':response.json()['target_key']}
        assert client.post(route,headers={'origin':ORIGIN},json=body).status_code == 403
        headers = {'origin':ORIGIN,'x-csrf-token':bootstrap['csrf_token']}
        assert client.post(route,headers=headers,json=body|{'env':'evil'}).status_code == 422
        assert client.post(route,headers=headers,json={'settings':SETTINGS|{'reserve_vram_gib':-1},'revision':'a'*64}).status_code == 422
        count = len(calls)
        assert client.post(route,headers=headers,json=body|{'target_key':'b'*64}).status_code == 409
        assert len(calls) == count
        app.state.connection._host_id = '[different-host]:22'
        assert client.post(route,headers=headers,json=body).status_code == 409
        assert len(calls) == count
        app.state.connection._host_id = None
        assert client.post(route,headers=headers,json=body).status_code == 200
        assert calls[-1] == ('POST','/api/v1/settings/memory',{'body':{'settings':SETTINGS,'revision':'a'*64}})
