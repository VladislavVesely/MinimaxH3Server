"""Local-only controller factory; the launcher must bind to loopback, not 0.0.0.0.

Supply the exact listener origin (including its port). Launch the browser at
``origin + '/#token=' + app.state.session.bootstrap_token``; the fragment never
enters HTTP access logs, and the bundled UI removes it before consumption.
No installer, generation service, or credential persistence is exposed here.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from importlib.resources import files
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
import re
import socket
from urllib.parse import urlsplit

from fastapi import Body, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
import paramiko
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from h3server.backends import load_profiles
from h3server.controller.session import BrowserSession
from h3server.controller.ssh import ControllerError, SSHConnection
from h3server.probe import probe_hardware
from h3server.profiles import recommend_profiles

API = "/api/v1/controller"
BODY_LIMIT = 16384
CAPABILITIES = {"scan": True, "installer": False, "generation": False}

WEB_ASSETS = {"/assets/app.js": ("app.js", "text/javascript"),
              "/assets/styles.css": ("styles.css", "text/css")}
SECURITY_HEADERS = {
    "cache-control": "no-store",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "content-security-policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
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
        if lengths and int(lengths[0]) > BODY_LIMIT:
            await reject("body_too_large", 413)
            return
        body = bytearray()
        try:
            async with asyncio.timeout(5):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > BODY_LIMIT:
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
    password: SecretStr = Field(min_length=1, max_length=4096)


class EmptyRequest(StrictRequest):
    pass


def create_app(*, state_dir: Path, origin: str, session: BrowserSession | None = None,
               connection: SSHConnection | None = None, socket_factory=socket.create_connection,
               transport_factory=paramiko.Transport) -> FastAPI:
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

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            session.invalidate()
            await run_in_threadpool(connection.close)

    app = FastAPI(title="H3 local read-only controller", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=API + "/openapi.json")
    app.state.session = session
    app.state.connection = connection
    app.add_middleware(LocalSecurity, origin=origin, session=session)

    @app.exception_handler(ControllerError)
    async def controller_error(request, exc):
        return _error(exc.code, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's default HTTP detail includes raw input, including passwords.
        return _error("invalid_request", 422)

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
        return {**connection.status(), "csrf_token": session.csrf_token, "capabilities": CAPABILITIES}

    @app.get(API + "/fingerprint")
    def fingerprint():
        return connection.status()

    @app.post(API + "/connect")
    def connect(body: ConnectRequest):
        return connection.connect(body.host, body.port, body.username)

    @app.post(API + "/approve")
    def approve(body: ApprovalRequest):
        return connection.approve(body.fingerprint)

    @app.post(API + "/authenticate")
    def authenticate(body: AuthenticationRequest):
        return connection.authenticate(body.password.get_secret_value())

    @app.post(API + "/disconnect")
    def disconnect(body: EmptyRequest | None = Body(default=None)):
        return connection.close()

    @app.post(API + "/scan")
    def scan(body: EmptyRequest | None = Body(default=None)):
        with connection.lock:
            home = connection.remote_home()
            probe = probe_hardware(connection, disk_path=home)
            recommendations = recommend_profiles(probe.hardware, load_profiles())
        return {"probe": {"hardware": asdict(probe.hardware), "kernel": probe.kernel,
                          "cuda": asdict(probe.cuda), "disk_path": probe.disk_path, "issues": probe.issues},
                "recommendations": [asdict(item) for item in recommendations],
                "compatibility": "Unverified compatibility: experimental pinned-source budgets are not proof of H3 fit or generation readiness.",
                "capabilities": CAPABILITIES}

    return app
