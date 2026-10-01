"""Exercise the real ASGI app; only its SSH network boundary is replaced."""
import asyncio
import json
import threading

import httpx
import paramiko
import pytest
from fastapi.testclient import TestClient

from test_ssh import Network

ORIGIN = "http://127.0.0.1:8123"
API = "/api/v1/controller"


@pytest.fixture
def controller(tmp_path):
    from h3server.controller.app import create_app
    network = Network()
    app = create_app(state_dir=tmp_path / "state", origin=ORIGIN,
                     socket_factory=network.socket, transport_factory=network.transport)
    with TestClient(app, base_url=ORIGIN) as client:
        yield app, client, network


def bootstrap(app, client):
    response = client.post(API + "/bootstrap", json={"token": app.state.session.bootstrap_token},
                           headers={"origin": ORIGIN})
    assert response.status_code == 200
    return {"origin": ORIGIN, "x-csrf-token": response.json()["csrf_token"]}


def connect(client, headers):
    return client.post(API + "/connect", json={"host": "example.org", "port": 22, "username": "test"}, headers=headers)


def test_bootstrap_one_time_http_only_session_and_url_cleanup(controller):
    app, client, _ = controller
    token = app.state.session.bootstrap_token
    root = client.get("/")
    assert root.status_code == 200 and token not in root.text
    script = client.get("/assets/app.js").text
    assert "history.replaceState" in script
    assert script.index("history.replaceState") < script.index("fetch(")
    assert "location.hash" in script
    assert root.headers["referrer-policy"] == "no-referrer"
    assert root.headers["cache-control"] == "no-store"
    assert client.get(API + "/status").status_code == 401
    response = client.post(API + "/bootstrap", json={"token": token}, headers={"origin": ORIGIN})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert "path=/" in cookie
    assert token not in repr(app.state.session)
    assert client.post(API + "/bootstrap", json={"token": token}, headers={"origin": ORIGIN}).status_code == 401
    assert client.get(API + "/status").status_code == 200
    assert client.get(API + "/bootstrap?token=" + token).status_code == 405


def test_expired_bootstrap_and_session(tmp_path):
    from h3server.controller.app import create_app
    from h3server.controller.session import BrowserSession
    now = [0.0]
    session = BrowserSession(clock=lambda: now[0], bootstrap_ttl=60, session_ttl=120)
    app = create_app(state_dir=tmp_path, origin=ORIGIN, session=session)
    with TestClient(app, base_url=ORIGIN) as client:
        token = session.bootstrap_token
        now[0] = 61
        assert client.post(API + "/bootstrap", json={"token": token}, headers={"origin": ORIGIN}).status_code == 401
    now[0] = 0
    session = BrowserSession(clock=lambda: now[0], bootstrap_ttl=60, session_ttl=120)
    app = create_app(state_dir=tmp_path, origin=ORIGIN, session=session)
    with TestClient(app, base_url=ORIGIN) as client:
        bootstrap(app, client)
        now[0] = 121
        assert client.get(API + "/status").status_code == 401


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil:8123", "localhost:8123", "127.0.0.1:9999", "127.0.0.1", "127.0.0.1:8123@evil"])
def test_exact_host_allowlist_blocks_rebinding(controller, host):
    _, client, network = controller
    assert client.get("/", headers={"host": host}).status_code == 403
    assert network.sockets == []


@pytest.mark.parametrize("origin", ["https://127.0.0.1:8123", "http://evil.example", "null", "http://127.0.0.1:8123/", "http://localhost:8123", "http://127.0.0.1:8123.evil"])
def test_foreign_origin_rejected_even_on_safe_routes(controller, origin):
    app, client, _ = controller
    bootstrap(app, client)
    assert client.get(API + "/status", headers={"origin": origin}).status_code == 403
    assert client.post(API + "/disconnect", headers={"origin": origin}).status_code == 403


def test_unsafe_routes_require_origin_cookie_and_csrf(controller):
    app, client, _ = controller
    assert client.post(API + "/bootstrap", json={"token": app.state.session.bootstrap_token}).status_code == 403
    headers = bootstrap(app, client)
    assert connect(client, {}).status_code == 403
    assert connect(client, {"origin": ORIGIN}).status_code == 403
    assert connect(client, {**headers, "x-csrf-token": "wrong"}).status_code == 403
    client.cookies.clear()
    assert connect(client, headers).status_code == 401


def test_full_readonly_workflow_and_experimental_recommendations(controller):
    app, client, network = controller
    headers = bootstrap(app, client)
    assert client.post(API + "/scan", headers=headers).status_code == 409
    response = connect(client, headers)
    assert response.status_code == 200
    fingerprint = response.json()["fingerprint"]
    assert response.json()["state"] == "awaiting_approval"
    assert network.auth_calls == []
    assert client.get(API + "/fingerprint").json()["fingerprint"] == fingerprint
    assert client.post(API + "/authenticate", json={"password": "secret"}, headers=headers).status_code == 409
    assert client.post(API + "/approve", json={"fingerprint": fingerprint}, headers=headers).status_code == 200
    assert network.auth_calls == []
    assert client.post(API + "/authenticate", json={"password": "secret"}, headers=headers).status_code == 200
    network.outputs.update({"LC_ALL=C uname -s": b"Linux", "LC_ALL=C uname -m": b"x86_64"})
    result = client.post(API + "/scan", headers=headers)
    assert result.status_code == 200
    data = result.json()
    assert data["probe"]["hardware"]["architecture"] == "x86_64"
    assert data["probe"]["disk_path"] == "/home/test"
    assert data["recommendations"]
    assert all(item["status"] != "verified" for item in data["recommendations"])
    assert "unverified" in data["compatibility"].lower()
    from h3server.backends.turbo import DIGEST as TURBO_DIGEST
    from h3server.backends.sparse import DIGEST as SPARSE_DIGEST
    assert data["capabilities"] == {"scan": True, "installer": True, "generation": True, "multi_runtime_setup": True, "audio_export": True,
                                    "turbo_artifact_digest": TURBO_DIGEST, "sparse_artifact_digest": SPARSE_DIGEST}
    assert "commands" not in data["probe"]  # no raw remote stderr/output leaked
    assert client.post(API + "/disconnect", headers=headers).json()["state"] == "disconnected"
    assert network.sockets[-1].closed


def test_reject_unknown_options_and_never_echo_secrets(controller, caplog):
    app, client, network = controller
    headers = bootstrap(app, client)
    for path, body in [("connect", {"host": "example.org", "port": 22, "username": "test", "password": "TOP-SECRET"}),
                       ("authenticate", {"password": ["TOP-SECRET"]}),
                       ("scan", {"command": "echo TOP-SECRET"}),
                       ("disconnect", {"ignored": "TOP-SECRET"})]:
        response = client.post(API + "/" + path, json=body, headers=headers)
        assert response.status_code == 422
        assert "TOP-SECRET" not in response.text
    challenge = connect(client, headers).json()
    client.post(API + "/approve", json={"fingerprint": challenge["fingerprint"]}, headers=headers)
    network.auth_error = paramiko.AuthenticationException("TOP-SECRET details")
    response = client.post(API + "/authenticate", json={"password": "TOP-SECRET"}, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_failed"
    assert "TOP-SECRET" not in response.text + caplog.text


def test_changed_host_key_is_typed_blocking_error(controller):
    app, client, network = controller
    headers = bootstrap(app, client)
    fingerprint = connect(client, headers).json()["fingerprint"]
    client.post(API + "/approve", json={"fingerprint": fingerprint}, headers=headers)
    client.post(API + "/disconnect", headers=headers)
    network.key = paramiko.RSAKey.generate(1024)
    response = connect(client, headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "host_key_changed"
    assert network.auth_calls == []


def test_body_limit_content_length_and_chunked(controller):
    app, client, _ = controller
    headers = bootstrap(app, client)
    assert client.post(API + "/connect", content=b"x" * 16385, headers=headers).status_code == 413

    async def request():
        messages = iter([
            {"type": "http.request", "body": b"x" * 10000, "more_body": True},
            {"type": "http.request", "body": b"x" * 10000, "more_body": False},
        ])
        sent = []
        async def receive():
            return next(messages)
        async def send(message):
            sent.append(message)
        await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                   "method": "POST", "scheme": "http", "path": API + "/connect", "raw_path": (API + "/connect").encode(),
                   "query_string": b"", "root_path": "", "server": ("127.0.0.1", 8123), "client": ("127.0.0.1", 1),
                   "headers": [(b"host", b"127.0.0.1:8123"), (b"origin", ORIGIN.encode()),
                               (b"cookie", f"h3_session={client.cookies['h3_session']}".encode()),
                               (b"x-csrf-token", headers["x-csrf-token"].encode()), (b"transfer-encoding", b"chunked")]}, receive, send)
        return sent
    sent = asyncio.run(request())
    assert sent[0]["status"] == 413


def test_duplicate_security_headers_fail_closed(controller):
    _, client, _ = controller
    assert client.get("/", headers=[("host", "127.0.0.1:8123"), ("host", "evil")]).status_code == 403
    assert client.get("/", headers=[("origin", ORIGIN), ("origin", "http://evil")]).status_code == 403


def test_scan_runs_off_event_loop(controller):
    app, client, network = controller
    headers = bootstrap(app, client)
    fingerprint = connect(client, headers).json()["fingerprint"]
    client.post(API + "/approve", json={"fingerprint": fingerprint}, headers=headers)
    client.post(API + "/authenticate", json={"password": "secret"}, headers=headers)

    async def scan():
        loop_thread = threading.get_ident()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                                     cookies=client.cookies) as async_client:
            response = await async_client.post(API + "/scan", headers=headers)
        assert response.status_code == 200
        assert network.thread_ids and all(item != loop_thread for item in network.thread_ids)
    asyncio.run(scan())


@pytest.mark.parametrize("origin", ["http://0.0.0.0:8123", "http://evil:8123", "http://127.0.0.1:8123/path", "http://user@127.0.0.1:8123", "http://127.0.0.1:8123/"])
def test_factory_rejects_noncanonical_nonloopback_origin(tmp_path, origin):
    from h3server.controller.app import create_app
    with pytest.raises(ValueError):
        create_app(state_dir=tmp_path, origin=origin)


def test_password_model_safe_repr():
    from h3server.controller.app import AuthenticationRequest
    assert "TOP-SECRET" not in repr(AuthenticationRequest(password="TOP-SECRET"))


def test_malformed_cookie_fails_closed(controller):
    _, client, _ = controller
    response = client.get(API + "/status", headers={"cookie": "h3_session=abc; @bad=x"})
    assert response.status_code == 401


def test_duplicate_cookies_and_csrf_fail_closed(controller):
    app, client, _ = controller
    headers = bootstrap(app, client)
    token = client.cookies["h3_session"]
    assert client.get(API + "/status", headers={"cookie": f"h3_session={token}; h3_session={token}"}).status_code == 401
    assert client.post(API + "/disconnect", headers=[("origin", ORIGIN), ("x-csrf-token", headers["x-csrf-token"]),
                                                        ("x-csrf-token", headers["x-csrf-token"])]).status_code == 403


def test_lifespan_closes_authenticated_transport(tmp_path):
    from h3server.controller.app import create_app
    network = Network()
    app = create_app(state_dir=tmp_path, origin=ORIGIN, socket_factory=network.socket, transport_factory=network.transport)
    with TestClient(app, base_url=ORIGIN) as client:
        headers = bootstrap(app, client)
        fingerprint = connect(client, headers).json()["fingerprint"]
        client.post(API + "/approve", json={"fingerprint": fingerprint}, headers=headers)
        client.post(API + "/authenticate", json={"password": "secret"}, headers=headers)
        cookie = client.cookies["h3_session"]
    assert network.sockets[0].closed and network.transports[0].closed
    assert not app.state.session.authenticated(cookie)


def test_malformed_unicode_tokens_are_rejected_not_server_errors(controller):
    app, client, _ = controller
    response = client.post(API + "/bootstrap", content=json.dumps({"token": "\ud800"}),
                           headers={"origin": ORIGIN, "content-type": "application/json"})
    assert response.status_code == 401
    headers = bootstrap(app, client)
    connect(client, headers)
    response = client.post(API + "/approve", content=json.dumps({"fingerprint": "\ud800"}),
                           headers={**headers, "content-type": "application/json"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_memory_recovery_consent_optout_disconnect_and_shutdown(controller, tmp_path, caplog):
    app, client, network = controller
    headers = bootstrap(app, client)
    fingerprint = connect(client, headers).json()['fingerprint']
    client.post(API + '/approve', json={'fingerprint': fingerprint}, headers=headers)
    result = client.post(API + '/authenticate', json={'password': 'MEMORY-ONLY-SECRET',
        'retain_for_setup_recovery': True}, headers=headers)
    assert result.json()['recovery']['credential_retained']
    assert 'MEMORY-ONLY-SECRET' not in result.text + repr(app.state.recovery) + caplog.text
    assert all(b'MEMORY-ONLY-SECRET' not in p.read_bytes() for p in tmp_path.rglob('*') if p.is_file())
    assert client.post(API + '/recovery/forget', json={}, headers=headers).json()['credential_retained'] is False
    assert client.post(API + '/disconnect', json={}, headers=headers).status_code == 200
    connect(client, headers)
    result = client.post(API + '/authenticate', json={'password': 'MEMORY-ONLY-SECRET'}, headers=headers)
    assert result.json()['recovery']['credential_retained'] is False  # old clients must opt in
    client.post(API + '/disconnect', json={}, headers=headers)
    connect(client, headers)
    client.post(API + '/authenticate', json={'password': 'MEMORY-ONLY-SECRET',
        'retain_for_setup_recovery': True}, headers=headers)
    app.state.session.invalidate()
    from test_controller_reconnection import eventually
    eventually(lambda: not app.state.recovery.status()['credential_retained'])
    assert client.get(API + '/status').status_code == 401


def test_failed_target_switch_cannot_bind_retained_password_to_another_host(controller):
    app, client, network = controller
    headers = bootstrap(app, client)
    fingerprint = connect(client, headers).json()['fingerprint']
    client.post(API + '/approve', json={'fingerprint': fingerprint}, headers=headers)
    assert client.post(API + '/connect', json={'host': 'other.example', 'username': 'test'}, headers=headers).status_code == 409
    result = client.post(API + '/authenticate', json={'password': 'SECRET',
        'retain_for_setup_recovery': True}, headers=headers)
    assert result.status_code == 200
    assert not result.json()['recovery']['credential_retained']
