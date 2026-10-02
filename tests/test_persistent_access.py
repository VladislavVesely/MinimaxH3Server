import concurrent.futures
import os

import pytest
from fastapi.testclient import TestClient

from h3server.controller.app import API, create_app
from h3server.controller.session import BrowserSession

ORIGIN = 'http://192.168.1.61:8787'
KEY = 'fixture-persistent-access-' + 'x' * 32


def test_default_session_has_no_deadline():
    now = [0.0]
    session = BrowserSession(clock=lambda: now[0])
    cookie, csrf = session.consume(session.bootstrap_token)
    now[0] += 50 * 365 * 24 * 60 * 60
    assert session.authenticated(cookie)
    assert session.csrf_valid(csrf)
    assert session.active
    session.invalidate()
    assert not session.authenticated(cookie)


def test_browser_cookie_survives_shutdown_and_restart(tmp_path):
    app = create_app(state_dir=tmp_path, origin=ORIGIN, lan_access_key=KEY)
    with TestClient(app, base_url=ORIGIN, client=('192.168.1.20', 1234)) as client:
        login = client.post(API + '/lan-login', headers={'origin': ORIGIN}, json={'token': KEY})
        cookie = client.cookies.get('h3_session')
        csrf = login.json()['csrf_token']
        assert 'HttpOnly' in login.headers['set-cookie']
        assert 'SameSite=strict' in login.headers['set-cookie']
        assert 'Max-Age=28800' not in login.headers['set-cookie']
    restarted = create_app(state_dir=tmp_path, origin=ORIGIN, lan_access_key=KEY)
    with TestClient(restarted, base_url=ORIGIN, client=('192.168.1.20', 1234)) as client:
        client.cookies.set('h3_session', cookie, domain='192.168.1.61', path='/')
        response = client.get(API + '/status')
        assert response.status_code == 200
        assert response.json()['csrf_token'] == csrf
        assert 'set-cookie' in response.headers
        assert client.post(API + '/disconnect', headers={'origin': ORIGIN, 'x-csrf-token': csrf}, json={}).status_code == 200
        client.cookies.clear()
        assert client.get(API + '/status').status_code == 401


def test_remembered_browser_restores_cookie_without_access_key(tmp_path):
    app = create_app(state_dir=tmp_path, origin=ORIGIN, lan_access_key=KEY)
    with TestClient(app, base_url=ORIGIN, client=('192.168.1.20', 1234)) as client:
        data = client.post(API + '/lan-login', headers={'origin': ORIGIN}, json={'token': KEY}).json()
        assert 'remember_token' in data
        grant = data['remember_token']
        assert grant != KEY
        assert KEY not in str(data)
    with TestClient(create_app(state_dir=tmp_path, origin=ORIGIN, lan_access_key=KEY), base_url=ORIGIN, client=('192.168.1.20', 1234)) as client:
        assert client.post(API + '/session/restore', json={'token': grant}).status_code == 403
        assert client.post(API + '/session/restore', headers={'origin': 'http://evil.test'}, json={'token': grant}).status_code == 403
        assert client.post(API + '/session/restore', headers={'origin': ORIGIN}, json={'token': 'wrong'}).status_code == 401
        response = client.post(API + '/session/restore', headers={'origin': ORIGIN}, json={'token': grant})
        assert response.status_code == 200
        assert client.get(API + '/status').status_code == 200
        assert client.post(API + '/disconnect', headers={'origin': ORIGIN}, json={}).status_code == 403
        app.state.session.invalidate()
        assert client.get(API + '/status').status_code == 401
        client.cookies.clear()
        assert client.post(API + '/session/restore', headers={'origin': ORIGIN}, json={'token': grant}).status_code == 401
        assert client.post(API + '/lan-login', headers={'origin': ORIGIN}, json={'token': KEY}).status_code == 401


def test_private_store_is_stable_concurrent_and_resettable(tmp_path):
    from h3server.controller.access import LocalAccess
    def initialize(_):
        return LocalAccess(tmp_path).initialize()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(initialize, range(16)))
    assert all(record == records[0] for record in records)
    store = LocalAccess(tmp_path)
    assert store.initialize() == records[0]
    if os.name == 'posix':
        assert (tmp_path / 'local-access.json').stat().st_mode & 0o777 == 0o600
    assert store.reset()['key'] != records[0]['key']
    assert LocalAccess(tmp_path).initialize() != records[0]


def test_reset_updates_the_stable_key_export(tmp_path):
    from h3server.controller.access import LocalAccess
    store = LocalAccess(tmp_path)
    old = store.initialize()
    path = store.export()
    assert old['key'] in path.read_text()
    assert store.export() == path
    new = store.reset()
    assert new['key'] in path.read_text()
    assert old['key'] not in path.read_text()


def test_initialization_is_serialized_across_processes(tmp_path):
    import subprocess
    import sys
    script = 'import sys,json,hashlib; from h3server.controller.access import LocalAccess; print(hashlib.sha256(json.dumps(LocalAccess(sys.argv[1]).initialize(),sort_keys=True).encode()).hexdigest())'
    def initialize(_):
        return subprocess.check_output([sys.executable, '-c', script, str(tmp_path)])
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        digests = list(pool.map(initialize, range(8)))
    assert len(set(digests)) == 1


@pytest.mark.parametrize('kind', ['symlink', 'world-readable', 'invalid', 'oversized', 'hardlink'])
def test_private_store_rejects_unsafe_existing_state(tmp_path, kind):
    from h3server.controller.access import LocalAccess
    store = LocalAccess(tmp_path)
    store.initialize()
    path = tmp_path / 'local-access.json'
    if kind == 'symlink':
        target = tmp_path / 'other.json'
        path.rename(target)
        path.symlink_to(target)
    elif kind == 'world-readable':
        if os.name != 'posix':
            pytest.skip('POSIX permissions')
        path.chmod(0o644)
    elif kind == 'hardlink':
        os.link(path, tmp_path / 'other.json')
    else:
        path.write_text('{}' if kind == 'invalid' else 'x' * 8192)
    with pytest.raises((ValueError, OSError)):
        store.initialize()
