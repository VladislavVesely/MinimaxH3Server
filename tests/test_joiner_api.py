import pytest
from test_controller_security import controller, bootstrap, API, ORIGIN
from test_joiner import clips


def test_joiner_routes_require_session_and_csrf(controller):
    app, client, _ = controller
    assert client.get(API+'/joiner').status_code == 401
    headers = bootstrap(app, client)
    assert client.get(API+'/joiner').status_code == 200
    assert client.post(API+'/joiner/clear', json={}, headers={'origin':ORIGIN}).status_code == 403
    assert client.post(API+'/joiner/export', json={'clips':['../../etc/passwd']}, headers=headers).status_code == 422
    assert client.post(API+'/joiner/clips', content=b'x', headers={**headers,'content-type':'application/octet-stream'}).status_code == 422


def test_conversion_requires_explicit_boolean_consent(controller, clips):
    app,client,_=controller;headers=bootstrap(app,client);ids=[]
    for path in (clips[0],clips[2]):
        response=client.post(API+'/joiner/clips',content=path.read_bytes(),headers={**headers,'content-type':'application/octet-stream'})
        ids.append(response.json()['id'])
    assert client.post(API+'/joiner/export',json={'clips':ids},headers=headers).json()['error']['code']=='joiner_incompatible'
    assert not app.state.joiner.exports
    assert client.post(API+'/joiner/export',json={'clips':ids,'convert':'true'},headers=headers).status_code==422
    response=client.post(API+'/joiner/export',json={'clips':ids,'convert':True},headers=headers)
    assert response.status_code==200 and response.json()['converted'] is True


def test_upload_preview_export_remove_and_cleanup(controller, clips):
    app, client, network = controller
    headers=bootstrap(app,client)
    ids=[]
    for path in clips[:2]:
        r=client.post(API+'/joiner/clips',content=path.read_bytes(),headers={**headers,'content-type':'application/octet-stream'})
        assert r.status_code==201, r.text
        ids.append(r.json()['id'])
    preview=API+'/joiner/clips/'+ids[0]+'/content'
    response=client.get(preview,headers={'range':'bytes=0-1'})
    assert response.status_code==206 and response.content==clips[0].read_bytes()[:2]
    assert response.headers['content-type']=='video/mp4'
    result=client.post(API+'/joiner/export',json={'clips':ids},headers=headers)
    assert result.status_code==200, result.text
    exported=client.get(API+'/joiner/exports/'+result.json()['id'])
    assert exported.status_code==200 and 'attachment' in exported.headers['content-disposition']
    assert client.post(API+'/joiner/clips/'+ids[0]+'/remove',json={},headers=headers).status_code==200
    assert client.get(preview).status_code==404
    assert client.post(API+'/joiner/clear',json={},headers=headers).status_code==200
    assert client.get(API+'/joiner').json()['retained_clips']==0
    assert not network.sockets, 'Local joining must not require SSH or mutate remote jobs'
