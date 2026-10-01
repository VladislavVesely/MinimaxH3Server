from test_controller_security import controller, bootstrap, API, ORIGIN


def test_character_proxy_and_csrf(controller,monkeypatch):
    from h3server.controller.proxy import RemoteProxy
    app,c,_=controller;calls=[]
    monkeypatch.setattr(RemoteProxy,'request',lambda self,method,path,**kw:calls.append((method,path,kw)) or {'characters':[]})
    url=API+'/remote/characters'
    assert c.get(url).status_code==401
    h=bootstrap(app,c)
    assert c.post(url,json={},headers={'origin':ORIGIN}).status_code==403
    assert c.get(url).status_code==200
    assert calls[-1][1]=='/api/v1/characters'
    assert c.post(url,json={'name':'Ada'},headers=h).status_code==200
    assert calls[-1][2]['body']=={'name':'Ada'}
    id='a'*32
    assert c.post(url+'/'+id+'/update',json={'revision':1},headers=h).status_code==200
    assert calls[-1][1]=='/api/v1/characters/'+id+'/update'
    count=len(calls)
    assert c.post(url+'/not-an-id/delete',json={'revision':1},headers=h).status_code==422
    assert len(calls)==count


def test_real_proxy_allows_only_confined_character_routes(controller):
    import pytest
    from h3server.controller.proxy import RemoteProxy
    from h3server.controller.ssh import ControllerError
    app,_,_=controller
    proxy=RemoteProxy(app.state.connection)
    for method,path in [('GET','/api/v1/characters'),('POST','/api/v1/characters'),('GET','/api/v1/characters/'+'a'*32),('POST','/api/v1/characters/'+'a'*32+'/update'),('POST','/api/v1/characters/'+'a'*32+'/delete')]:
        with pytest.raises(ControllerError) as error:proxy.request(method,path)
        assert error.value.code!='proxy_route_forbidden'
    with pytest.raises(ControllerError) as error:proxy.request('POST','/api/v1/characters/not-an-id/delete')
    assert error.value.code=='proxy_route_forbidden'
