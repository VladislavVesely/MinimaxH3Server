import pytest
from fastapi.testclient import TestClient
from h3server.controller.app import create_app,API

ORIGIN='http://192.168.1.61:8787'
KEY='fixture-lan-access-key-'+'x'*32


def test_lan_requires_explicit_enable_and_private_address(tmp_path):
    with pytest.raises(ValueError):create_app(state_dir=tmp_path,origin=ORIGIN)
    for origin in ('http://0.0.0.0:8787','http://8.8.8.8:8787','http://evil.test:8787'):
        with pytest.raises(ValueError):create_app(state_dir=tmp_path,origin=origin,lan_access_key=KEY)


def test_lan_login_host_origin_csrf_and_multiple_browsers(tmp_path):
    app=create_app(state_dir=tmp_path,origin=ORIGIN,lan_access_key=KEY)
    with TestClient(app,base_url=ORIGIN,client=('192.168.1.20',1234)) as client:
        assert client.get(API+'/access').json()=={'lan':True}
        assert client.get(API+'/status').status_code==401
        assert client.post(API+'/lan-login',json={'token':KEY}).status_code==403
        assert client.post(API+'/lan-login',headers={'origin':ORIGIN},json={'token':'wrong'}).status_code==401
        response=client.post(API+'/lan-login',headers={'origin':ORIGIN},json={'token':KEY})
        assert response.status_code==200
        assert KEY not in response.text
        assert client.get(API+'/status').status_code==200
        assert client.post(API+'/disconnect',headers={'origin':ORIGIN},json={}).status_code==403
        assert client.get(API+'/status',headers={'host':'evil.test:8787'}).status_code==403
        assert client.get(API+'/status',headers={'origin':'http://evil.test'}).status_code==403
        client.cookies.clear()
        assert client.post(API+'/lan-login',headers={'origin':ORIGIN},json={'token':KEY}).status_code==200
    with TestClient(create_app(state_dir=tmp_path,origin=ORIGIN,lan_access_key=KEY),base_url=ORIGIN,client=('8.8.8.8',1234)) as client:
        assert client.get('/').status_code==403


def test_lan_login_is_bounded_and_disabled_by_default(tmp_path):
    with TestClient(create_app(state_dir=tmp_path,origin='http://127.0.0.1:8787'),base_url='http://127.0.0.1:8787') as client:
        assert client.get(API+'/access').json()=={'lan':False}
        assert client.post(API+'/lan-login',headers={'origin':'http://127.0.0.1:8787'},json={'token':KEY}).status_code==401
    with TestClient(create_app(state_dir=tmp_path,origin=ORIGIN,lan_access_key=KEY),base_url=ORIGIN,client=('192.168.1.20',1234)) as client:
        for _ in range(10):assert client.post(API+'/lan-login',headers={'origin':ORIGIN},json={'token':'wrong'}).status_code==401
        assert client.post(API+'/lan-login',headers={'origin':ORIGIN},json={'token':KEY}).status_code==429
