"""Local-only controller factory; the launcher must bind to loopback, not 0.0.0.0.

Supply the exact listener origin (including its port). Launch the browser at
``origin + '/#token=' + app.state.session.bootstrap_token``; the fragment never
enters HTTP access logs, and the bundled UI removes it before consumption.
Deployment uses digest-bound approvals; the remote API travels over SSH only.
SSH credentials may be retained with consent in local controller memory for setup
recovery and explicitly requested composite setup/sudo. Neither enters stored records.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from functools import wraps
from importlib.resources import files
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
import re
import socket
from urllib.parse import urlsplit
from typing import Literal

from fastapi import Body, FastAPI, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
import paramiko
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from h3server.backends import load_profiles
from h3server.backends.turbo import DIGEST as TURBO_DIGEST
from h3server.backends.sparse import DIGEST as SPARSE_DIGEST
from h3server.controller.deploy import Deployment, profile_catalog
from h3server.controller.proxy import RemoteProxy
from h3server.controller.reconnection import SetupRecovery
from h3server.installer.planner import Approval
from h3server.controller.session import BrowserSession
from h3server.controller.setup import SetupCoordinator
from h3server.controller.ssh import ControllerError, SSHConnection
from h3server.probe import probe_hardware
from h3server.profiles import recommend_profiles

API = "/api/v1/controller"
BODY_LIMIT = 16384
CAPABILITIES = {"scan": True, "installer": True, "generation": True, "turbo_artifact_digest": TURBO_DIGEST, "sparse_artifact_digest": SPARSE_DIGEST}

WEB_ASSETS = {"/assets/app.js": ("app.js", "text/javascript"),
              "/assets/job-timing.js": ("job-timing.js", "text/javascript"),
              "/assets/styles.css": ("styles.css", "text/css")}
SECURITY_HEADERS = {
    "cache-control": "no-store",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "content-security-policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; media-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
}


def _error(code: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code}}, status_code=status)


class LocalSecurity:
    """Validate before parsing: exact authority/origin, session, CSRF, bounded body.

    Buffering is bounded even without Content-Length (including chunked ASGI
    messages). A total receive deadline also prevents an endless slow body.
    """

    def __init__(self, app, *, origin: str, session: BrowserSession):
        self.app = app
        self.origin = origin
        self.authority = urlsplit(origin).netloc
        self.session = session

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def secured_send(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.decode().lower() not in SECURITY_HEADERS]
                message = {**message, "headers": headers + [(k.encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]}
            await send(message)

        async def reject(code, status):
            await _error(code, status)(scope, receive, secured_send)

        headers = {}
        for name, value in scope["headers"]:
            headers.setdefault(name.decode("latin-1").lower(), []).append(value.decode("latin-1"))
        if headers.get("host") != [self.authority] or scope.get("scheme") != "http":
            await reject("host_forbidden", 403)
            return
        origin = headers.get("origin")
        unsafe = scope["method"] not in {"GET", "HEAD", "OPTIONS"}
        if (origin is not None and origin != [self.origin]) or (unsafe and origin != [self.origin]):
            await reject("origin_forbidden", 403)
            return
        if unsafe and scope.get("query_string"):
            await reject("invalid_request", 422)
            return
        bootstrap = scope["path"] == API + "/bootstrap"
        public = (scope["path"] == "/" or scope["path"] in WEB_ASSETS) and scope["method"] in {"GET", "HEAD"}
        if not public and not bootstrap:
            cookie_headers = headers.get("cookie", [])
            cookies = SimpleCookie()
            try:
                if len(cookie_headers) != 1:
                    raise ValueError()
                if sum(part.strip().startswith("h3_session=") for part in cookie_headers[0].split(";")) != 1:
                    raise ValueError()
                cookies.load(cookie_headers[0])
                cookie = cookies["h3_session"].value
            except (ValueError, KeyError, CookieError):
                cookie = ""
            if not self.session.authenticated(cookie):
                await reject("session_required", 401)
                return
            csrf = headers.get("x-csrf-token", [])
            if unsafe and (len(csrf) != 1 or not self.session.csrf_valid(csrf[0])):
                await reject("csrf_forbidden", 403)
                return
        lengths = headers.get("content-length", [])
        if (len(lengths) > 1 or (lengths and not re.fullmatch(r"[0-9]{1,10}", lengths[0]))
                or (lengths and "transfer-encoding" in headers)):
            await reject("invalid_body", 400)
            return
        asset_upload = scope['method']=='POST' and re.fullmatch(re.escape(API)+r'/remote/assets/(?:image|video|audio)',scope['path'])
        generation = scope['method']=='POST' and scope['path'] in (API+'/remote/jobs', API+'/remote/performance/estimate')
        limit = 64*1024*1024 if asset_upload else 96*1024 if generation else BODY_LIMIT
        if lengths and int(lengths[0]) > limit:
            await reject("body_too_large", 413)
            return
        body = bytearray()
        try:
            async with asyncio.timeout(90 if asset_upload else 5):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > limit:
                        await reject("body_too_large", 413)
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await reject("body_timeout", 408)
            return
        if lengths and len(body) != int(lengths[0]):
            await reject("invalid_body", 400)
            return
        delivered = False

        async def buffered_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, buffered_receive, secured_send)


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class BootstrapRequest(StrictRequest):
    token: SecretStr


class ConnectRequest(StrictRequest):
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=65)


class ApprovalRequest(StrictRequest):
    fingerprint: str = Field(min_length=1, max_length=100)


class AuthenticationRequest(StrictRequest):
    remember_connection: bool = False
    retain_for_setup_recovery: bool = False
    reuse_for_setup: bool = False
    password: SecretStr = Field(min_length=1, max_length=4096)


class PreferencesRequest(StrictRequest):
    install_sage: bool
    use_sage: bool
    install_turbo: bool | None = None
    use_turbo: bool | None = None
    install_sparse: bool | None = None
    sparse_preset: Literal['off', 'quality', 'balanced', 'draft'] | None = None


class EmptyRequest(StrictRequest):
    pass


class SetupStartRequest(StrictRequest):
    sage_attention: bool = False
    turbo_lora: bool = False
    sparse_attention: bool = False
    profile: str = Field(min_length=1, max_length=100)
    artifact_set_digest: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{8,64}$')


class SetupRetryRequest(SetupStartRequest):
    plan_digest: str = Field(pattern=r'^[a-f0-9]{64}$')


class PlanRequest(StrictRequest):
    profile: str = Field(min_length=1, max_length=100)
    sudo_password_available: bool = False


class InstallRequest(StrictRequest):
    plan_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    license_acknowledged: bool = False
    eligibility_confirmed: bool = False
    privileged: bool = False
    reboot: bool = False
    service_digest: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    sudo_password: SecretStr | None = Field(default=None, min_length=1, max_length=4096)

    def approval(self):
        return Approval(**{k: getattr(self, k) for k in Approval.__dataclass_fields__})


class ActionRequest(StrictRequest):
    plan_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    activation_approved: bool = False
    rollback_approved: bool = False
    policy: str = Field(default='reject', pattern=r'^(reject|drain|cancel)$')
    cancel_confirmed: bool = False
    license_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    artifact_set_digest: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    license_acknowledged: bool = False
    eligibility_confirmed: bool = False
    download_approved: bool = False


def create_app(*, state_dir: Path, origin: str, session: BrowserSession | None = None,
               connection: SSHConnection | None = None, socket_factory=socket.create_connection,
               transport_factory=paramiko.Transport, release=None) -> FastAPI:
    """No network/FS actions at construction; all SSH/scan IO runs in workers.

    A supplied connection/session supports embedding and tests. Credentials are
    accepted only at the explicit authenticate step after host-key trust.
    """
    match = re.fullmatch(r"http://(127\.0\.0\.1|localhost|\[::1\]):([1-9][0-9]{0,4})", origin)
    if not match or int(match[2]) > 65535:
        raise ValueError("origin must be an exact loopback HTTP origin with an explicit port")
    session = session if session is not None else BrowserSession()
    connection = connection if connection is not None else SSHConnection(
        state_dir, socket_factory=socket_factory, transport_factory=transport_factory)

    async def expire_credentials():
        while True:
            recovery.expire()
            await run_in_threadpool(setup.expire)
            await run_in_threadpool(recovery.observe)
            await asyncio.sleep(.1)

    @asynccontextmanager
    async def lifespan(app):
        expiry = asyncio.create_task(expire_credentials())
        try:
            yield
        finally:
            session.invalidate()
            recovery.expire()
            expiry.cancel()
            await run_in_threadpool(setup.close)
            await run_in_threadpool(recovery.close)
            await run_in_threadpool(connection.close)

    from h3server.controller.preferences import Preferences
    preferences = Preferences(state_dir)
    deployment = Deployment(connection, release)
    recovery = SetupRecovery(connection, deployment, lambda: session.active)
    setup = SetupCoordinator(deployment, recovery, lambda: session.active)
    proxy = RemoteProxy(connection)
    setup.sage_request = lambda method: proxy.request_json(method, '/api/v1/features/sageattention')
    setup.turbo_request = lambda method: proxy.request_json(method, '/api/v1/features/turbo', body={'artifact_digest': TURBO_DIGEST})
    setup.sparse_request = lambda method: proxy.request_json(method, '/api/v1/features/sparse', body={'artifact_digest': SPARSE_DIGEST})

    def setup_idle(*, cancel=False):
        if setup.active():
            if cancel:
                setup.cancel()
            raise ControllerError('setup_in_progress')

    def observed(function):
        @wraps(function)
        def call(*args, **kwargs):
            with recovery.operation():
                setup_idle()
                try:
                    return function(*args, **kwargs)
                except Exception:
                    if connection.status()['state'] == 'disconnected':
                        recovery.start()
                    raise
        return call
    app = FastAPI(title="H3 local controller", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=API + "/openapi.json")
    app.state.session = session
    app.state.connection = connection
    app.state.deployment = deployment
    app.state.recovery = recovery
    app.state.setup = setup
    app.add_middleware(LocalSecurity, origin=origin, session=session)

    @app.exception_handler(ControllerError)
    async def controller_error(request, exc):
        return _error(exc.code, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's default HTTP detail includes raw input, including passwords.
        return _error("invalid_request", 422)

    @app.exception_handler(OSError)
    @app.exception_handler(ValueError)
    async def operation_error(request, exc):
        return _error('operation_failed', 409)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return _error("http_error", exc.status_code)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def root():
        return HTMLResponse(files("h3server").joinpath("web", "index.html").read_text(encoding="utf-8"))

    @app.get("/assets/{name}", include_in_schema=False)
    async def asset(name: str):
        selected = WEB_ASSETS.get("/assets/" + name)
        if selected is None:
            raise HTTPException(status_code=404)
        filename, media_type = selected
        return Response(files("h3server").joinpath("web", filename).read_bytes(), media_type=media_type)

    @app.post(API + "/bootstrap")
    async def bootstrap(body: BootstrapRequest):
        tokens = session.consume(body.token.get_secret_value())
        if tokens is None:
            raise ControllerError("bootstrap_invalid", 401)
        cookie, csrf = tokens
        response = JSONResponse({"csrf_token": csrf})
        response.set_cookie("h3_session", cookie, httponly=True, samesite="strict", path="/",
                            max_age=int(session.session_ttl))
        return response

    @app.get(API + "/status")
    def status():
        with recovery.operations:
            recovery.expire()
            if setup.active():
                return {**setup.connection_status(),
                        'recovery': recovery.status(), 'setup': setup.status(),
                        'csrf_token': session.csrf_token,
                        'capabilities': {**CAPABILITIES, 'release_available': release is not None}}
            if recovery.active():
                state = {'state': 'disconnected'}
                recovery_state = recovery.status()
                if recovery_state['state'] == 'recovered':
                    recovery_state['state'] = 'connecting'  # worker is finishing cleanup
            else:
                state = connection.status()
                if state['state'] == 'disconnected':
                    recovery.start()
                recovery_state = recovery.status()
                if recovery_state['state'] == 'recovered':
                    state = connection.status()
            return {**state, 'recovery': recovery_state, 'setup': setup.status(), "csrf_token": session.csrf_token,
                    "capabilities": {**CAPABILITIES, 'release_available': release is not None}}

    @app.get(API + "/fingerprint")
    def fingerprint():
        return status()

    @app.post(API + "/connect")
    def connect(body: ConnectRequest):
        with recovery.operations:
            setup_idle(cancel=True)
            with recovery.operation(replace=True):
                deployment.reset()
                setup.reset()
                recovery.set_target(None)
                result = connection.connect(body.host, body.port, body.username)
                recovery.set_target((body.host, body.port, body.username))
                return result

    @app.post(API + "/approve")
    @observed
    def approve(body: ApprovalRequest):
        return connection.approve(body.fingerprint)

    @app.post(API + "/authenticate")
    def authenticate(body: AuthenticationRequest):
        with recovery.operations:
            setup_idle()
            with recovery.operation(replace=True):
                result = connection.authenticate(body.password.get_secret_value())
                recovery.remember(body.password.get_secret_value(),
                                  enabled=body.retain_for_setup_recovery or body.reuse_for_setup,
                                  reuse_for_setup=body.reuse_for_setup)
                if body.remember_connection:
                    preferences.remember(recovery.target(), body.password.get_secret_value())
                return {**result, 'recovery': recovery.status()}

    @app.get(API + '/preferences')
    def get_preferences():
        return preferences.public()

    @app.post(API + '/preferences')
    def save_preferences(body: PreferencesRequest):
        return preferences.settings(body.install_sage, body.use_sage, body.install_turbo, body.use_turbo, body.install_sparse, body.sparse_preset)

    @app.post(API + '/connection/forget')
    def forget_connection(body: EmptyRequest):
        return preferences.forget()

    @app.post(API + '/connection/authenticate-saved')
    def authenticate_saved(body: EmptyRequest):
        with recovery.operations:
            saved = preferences.saved()
            if saved is None:
                raise ControllerError('saved_connection_missing')
            if recovery.target() != (saved['host'], saved['port'], saved['username']):
                raise ControllerError('saved_connection_mismatch')
            return authenticate(AuthenticationRequest(password=saved['password'], reuse_for_setup=True, remember_connection=True))

    @app.post(API + '/connection/restore')
    def restore_connection(body: EmptyRequest):
        with recovery.operations:
            saved = preferences.saved()
            if saved is None:
                raise ControllerError('saved_connection_missing')
            result = connect(ConnectRequest(**{k: saved[k] for k in ('host', 'port', 'username')}))
            # Never send the saved password until the stored host key matched.
            if result['state'] == 'awaiting_authentication':
                return authenticate_saved(body)
            return result

    @app.post(API + "/disconnect")
    def disconnect(body: EmptyRequest | None = Body(default=None)):
        with recovery.operations:
            setup_idle(cancel=True)
            with recovery.operation(replace=True):
                deployment.reset()
                setup.reset()
                return connection.close()

    @app.post(API + '/recovery/forget')
    def forget_recovery(body: EmptyRequest):
        with recovery.operations:
            setup_idle(cancel=True)
            with recovery.operation(replace=True):
                return recovery.status()

    @app.get(API + '/profiles')
    def profiles():
        return profile_catalog()

    @app.post(API + "/scan")
    @observed
    def scan(body: EmptyRequest | None = Body(default=None)):
        with connection.lock:
            home = connection.remote_home()
            probe = probe_hardware(connection, disk_path=home)
            recommendations = recommend_profiles(probe.hardware, load_profiles(include_pruned=True))
        return {"probe": {"hardware": asdict(probe.hardware), "kernel": probe.kernel,
                          "cuda": asdict(probe.cuda), "disk_path": probe.disk_path, "issues": probe.issues},
                "recommendations": [asdict(item) for item in recommendations],
                **profile_catalog(probe.hardware),
                "compatibility": "Unverified compatibility: experimental pinned-source budgets are not proof of H3 fit or generation readiness.",
                "capabilities": CAPABILITIES}

    @app.post(API + '/setup/start', status_code=202)
    def setup_start(body: SetupStartRequest):
        return setup.start(body.profile, body.artifact_set_digest, body.request_id, sage_attention=body.sage_attention, turbo_lora=body.turbo_lora, sparse_attention=body.sparse_attention)

    @app.post(API + '/setup/retry', status_code=202)
    def retry_setup(body: SetupRetryRequest):
        return setup.start(body.profile, body.artifact_set_digest, body.request_id, retry_digest=body.plan_digest, sage_attention=body.sage_attention, turbo_lora=body.turbo_lora, sparse_attention=body.sparse_attention)

    @app.post(API + '/setup/observe')
    @observed
    def observe_setup(body: EmptyRequest):
        return deployment.observe_saved()

    @app.post(API + '/setup/restore-compatible')
    @observed
    def restore_compatible(body: EmptyRequest):
        document = deployment.restore_compatible_failure()
        if document is None:
            raise ControllerError('matching_release_required')
        return {**document, 'snapshot': deployment.snapshot()}

    @app.get(API + '/setup/status')
    def setup_status():
        setup.expire()
        return setup.status()

    @app.post(API + '/setup/cancel', status_code=202)
    def setup_cancel(body: EmptyRequest):
        return setup.cancel()

    @app.post(API + '/setup/plan')
    @observed
    def setup_plan(body: PlanRequest):
        return deployment.make_plan(body.profile, sudo_password_available=body.sudo_password_available)

    @app.post(API + '/setup/install')
    @observed
    def setup_install(body: InstallRequest):
        return deployment.install(body.approval(), service_digest=body.service_digest,
            sudo_password=body.sudo_password.get_secret_value() if body.sudo_password else None)

    @app.post(API + '/setup/restore')
    @observed
    def setup_restore(body: EmptyRequest):
        return deployment.restore(observation_only=True, include_service=False)

    @app.get(API + '/setup/events')
    @observed
    def setup_events(after: int = Query(default=0, ge=0, le=2**63-1)):
        return deployment.snapshot(after)

    @app.post(API + '/setup/reboot')
    @observed
    def setup_reboot(body: InstallRequest):
        data = body.model_dump(exclude={'service_digest', 'sudo_password'})
        data['sudo_password'] = body.sudo_password.get_secret_value() if body.sudo_password else None
        result = deployment.action('reboot', data)
        automatic = recovery.start()
        return {**result, 'reauthentication_required': not automatic, 'recovery': recovery.status()}

    @app.post(API + '/setup/{action}')
    @observed
    def setup_action(action: str, body: ActionRequest):
        if action not in {'activate', 'download', 'cancel-download', 'repair', 'resume'}:
            raise ControllerError('unknown_action', 404)
        return deployment.action(action, body.model_dump())

    @app.get(API + '/remote/features/sparse')
    @observed
    def sparse_status():
        return proxy.request('GET', '/api/v1/features/sparse')

    @app.post(API + '/remote/features/sparse')
    @observed
    def install_sparse(body: EmptyRequest):
        return proxy.request('POST', '/api/v1/features/sparse', body={'artifact_digest': SPARSE_DIGEST})

    @app.get(API + '/remote/features/turbo')
    @observed
    def turbo_status():
        return proxy.request('GET', '/api/v1/features/turbo')

    @app.post(API + '/remote/features/turbo')
    @observed
    def install_turbo(body: EmptyRequest):
        return proxy.request('POST', '/api/v1/features/turbo', body={'artifact_digest': TURBO_DIGEST})

    @app.get(API + '/remote/features/sageattention')
    @observed
    def sage_status():
        return proxy.request('GET', '/api/v1/features/sageattention')

    @app.post(API + '/remote/features/sageattention')
    @observed
    def install_sage(body: EmptyRequest):
        return proxy.request('POST', '/api/v1/features/sageattention', body={})

    @app.get(API + '/remote/features/reference')
    @observed
    def remote_reference_status():
        return proxy.request('GET','/api/v1/features/reference')

    @app.post(API + '/remote/features/reference')
    @observed
    def remote_reference_install(body: dict = Body()):
        return proxy.request('POST','/api/v1/features/reference',body=body)

    @app.post(API + '/remote/assets/{kind}')
    @observed
    def remote_asset_upload(kind: str, body: bytes = Body(media_type='application/octet-stream')):
        return proxy.request('POST', f'/api/v1/assets/{kind}', binary=body)

    @app.get(API + '/remote/assets/{id}')
    @observed
    def remote_asset_metadata(id: str):
        return proxy.request('GET',f'/api/v1/assets/{id}')

    @app.get(API + '/remote/assets/{id}/content')
    @observed
    def remote_asset_content(id: str, request: Request):
        return proxy.request('GET',f'/api/v1/assets/{id}/content',range_header=request.headers.get('range'))

    @app.get(API + '/remote/models')
    @observed
    def remote_models():
        return proxy.request('GET', '/api/v1/models')

    @app.post(API + '/remote/models/unload')
    @observed
    def remote_unload(body: EmptyRequest):
        return proxy.request('POST', '/api/v1/models/unload', body={})

    @app.post(API + '/remote/performance/estimate')
    @observed
    def remote_estimate(body: dict = Body()):
        return proxy.request('POST', '/api/v1/performance/estimate', body=body)

    @app.get(API + '/remote/performance')
    @observed
    def remote_performance(offset: int = Query(default=0, ge=0, le=2**31-1)):
        return proxy.request('GET', f'/api/v1/performance?offset={offset}')

    @app.get(API + '/remote/health')
    @observed
    def remote_health():
        return proxy.request('GET', '/api/v1/health')

    @app.get(API + '/remote/capabilities')
    @observed
    def remote_capabilities():
        return proxy.request('GET', '/api/v1/capabilities')

    @app.get(API + '/remote/jobs')
    @observed
    def remote_jobs():
        return proxy.request('GET', '/api/v1/jobs?limit=1000')

    @app.get(API + '/remote/events')
    @observed
    def remote_events(after: int = Query(default=0, ge=0, le=2**63-1)):
        return proxy.request('GET', f'/api/v1/events?after={after}&follow=false')

    @app.post(API + '/remote/jobs')
    @observed
    def remote_submit(body: dict = Body()):
        # Remote GenerationInput is authoritative; never forward auth/origin headers.
        return proxy.request('POST', '/api/v1/jobs', body=body)

    @app.post(API + '/remote/jobs/{job_id}/delete')
    @observed
    def remote_delete(job_id: str, body: EmptyRequest):
        return proxy.request('POST', f'/api/v1/jobs/{job_id}/delete', body={})

    @app.post(API + '/remote/jobs/{job_id}/cancel')
    @observed
    def remote_cancel(job_id: str, body: EmptyRequest):
        return proxy.request('POST', f'/api/v1/jobs/{job_id}/cancel', body={})

    @app.get(API + '/remote/jobs/{job_id}/last-frame')
    @observed
    def remote_last_frame(job_id: str):
        return proxy.request('GET', f'/api/v1/jobs/{job_id}/last-frame')

    @app.get(API + '/remote/jobs/{job_id}/video')
    @observed
    def remote_video(job_id: str, request: Request, download: bool = False):
        return proxy.request('GET', f'/api/v1/jobs/{job_id}/video' + ('?download=true' if download else ''), range_header=request.headers.get('range'))

    return app
