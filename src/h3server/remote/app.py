"""Authenticated versioned remote API. No models or inference imports here.

The controller reads config/remote-token over authenticated SSH/SFTP and sends
Authorization: Bearer on every request, including SSE/video/OpenAPI. Tokens must
never be placed in URL queries, command arguments, browser URLs or logs. API
errors contain fixed codes; Pydantic's reflected inputs are not exposed.
"""
from contextlib import asynccontextmanager
from dataclasses import asdict
import asyncio
import json
import math
import os
import re
import secrets
import stat
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException, Path as APIPath, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator
from starlette.background import BackgroundTask

from h3server.domain import GenerationSettings, H3_PROFILES, JobRequest
from h3server.events import ReplayGap
from h3server.paths import AppPaths
from h3server.remote.worker import BackendUnavailable, QueueFull, Worker, _directory
from h3server.store import Store


class MaintenanceInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    operation: str = Field(pattern=r'^[a-f0-9]{32}$')
    policy: Literal['reject', 'drain', 'cancel'] = 'reject'
    cancel_confirmed: StrictBool = False


class GenerationInput(BaseModel):
    """Canvas area<=1,032,192 is service policy, not an upstream hard maximum.

    Frames round UP to 17*n+5. Nominal 15 seconds is invalid after alignment.
    Steps are sigma-grid points; denoising progress reports measured model calls.
    """
    model_config = ConfigDict(extra="forbid", strict=True)
    prompt: Annotated[StrictStr, Field(min_length=1, max_length=16000)]
    num_frames: Annotated[StrictInt | None, Field(ge=120, le=345)] = None
    duration: Annotated[StrictFloat | StrictInt | None, Field(ge=5, le=15)] = None
    width: Annotated[StrictInt, Field(ge=32, le=1344)] = 1344
    height: Annotated[StrictInt, Field(ge=32, le=1344)] = 768
    steps: Annotated[StrictInt, Field(ge=2, le=1000)] = 50
    seed: Annotated[StrictInt, Field(ge=0, le=2**63 - 1)] = 42
    profile: Literal["h3-bf16-cpu-offload-experimental", "h3-int8-group-offload-experimental"] = H3_PROFILES[0]
    loras: Annotated[list[StrictStr], Field(max_length=8)] = Field(default_factory=list)
    sparse_attention: StrictBool | None = None

    @model_validator(mode="after")
    def normalized(self):
        if "num_frames" in self.model_fields_set and "duration" in self.model_fields_set:
            raise ValueError("provide frames or duration, not both")
        if "sparse_attention" in self.model_fields_set:
            raise ValueError("sparse attention is unsupported")
        if not self.prompt.strip() or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item) for item in self.loras):
            raise ValueError("invalid prompt or logical LoRA identifier")
        for name in ("num_frames", "duration"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError("explicit null is not a generation value")
        self.to_request()
        return self

    def to_request(self) -> JobRequest:
        frames = self.num_frames if self.num_frames is not None else (self.duration * 24 if self.duration is not None else 124)
        aligned = math.ceil((frames - 5) / 17) * 17 + 5
        settings = GenerationSettings(aligned, self.width, self.height, self.steps, self.seed, self.profile)
        return JobRequest(self.prompt, tuple(self.loras), self.sparse_attention, settings)


def load_token(paths: AppPaths) -> str:
    """Generate/read a 0600 owner-only token; fail closed on links or broad modes."""
    with _directory(paths.root, "config") as directory:
        try:
            fd = os.open("remote-token", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w") as stream:
                stream.write(secrets.token_urlsafe(48) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(directory)
        fd = os.open("remote-token", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "r") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("remote token requires an owner-only regular file")
            token = stream.read(257).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
        raise ValueError("invalid remote token configuration")
    return token


def _job_data(store, job):
    job, output = store.get_job_snapshot(job.job_id)
    value = asdict(job)
    if job.request.settings is not None:
        value["effective_duration"] = job.request.settings.duration
    value["output"] = asdict(output) if output else None
    return value


def _range(value: str | None, size: int) -> tuple[int, int, int]:
    if value is None:
        return 0, size - 1, 200
    match = re.fullmatch(r"bytes=(\d{0,20})-(\d{0,20})", value)
    if not match or not any(match.groups()):
        raise ValueError("unsupported byte range")
    first, last = match.groups()
    if not first:
        length = int(last)
        if not length:
            raise ValueError("invalid suffix range")
        start, end = max(0, size - length), size - 1
    else:
        start, end = int(first), min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        raise ValueError("unsatisfied byte range")
    return start, end, 206


def create_app(store: Store, worker: Worker, *, token: str, heartbeat_seconds: float = 15) -> FastAPI:
    if len(token) < 32 or not token.isascii():
        raise ValueError("remote API token is too short or malformed")
    if not 0 < heartbeat_seconds <= 60:
        raise ValueError("invalid heartbeat interval")

    @asynccontextmanager
    async def lifespan(app):
        worker.start()
        try:
            yield
        finally:
            await asyncio.to_thread(worker.stop)

    app = FastAPI(title="MiniMax H3 remote API", version="1.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.state.request_model = GenerationInput

    @app.middleware("http")
    async def authenticate(request, call_next):
        supplied = request.headers.get("authorization", "").encode("utf-8")
        if not secrets.compare_digest(supplied, ("Bearer " + token).encode("ascii")):
            return JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        # Browser cross-origin calls are not a supported remote transport.
        if request.headers.get("origin") is not None:
            return JSONResponse({"error": "origin_not_allowed"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        # Even unknown-field names and validation context can contain credentials.
        return JSONResponse({"error": "invalid_request"}, status_code=422)

    @app.exception_handler(Exception)
    async def internal_error(request, error):
        return JSONResponse({"error": "internal_error"}, status_code=500)

    @app.get('/api/v1/maintenance')
    def maintenance_status():
        return worker.maintenance_status()

    @app.post('/api/v1/maintenance/begin')
    def maintenance_begin(payload: MaintenanceInput):
        try:
            return worker.begin_maintenance(payload.operation, policy=payload.policy, cancel_confirmed=payload.cancel_confirmed)
        except ValueError:
            raise HTTPException(409, 'maintenance_busy_or_consent_required') from None

    @app.post('/api/v1/maintenance/end')
    def maintenance_end(payload: MaintenanceInput):
        try:
            return worker.end_maintenance(payload.operation)
        except ValueError:
            raise HTTPException(409, 'maintenance_owner_mismatch') from None

    @app.get("/api/v1/health")
    def health():
        reasons = worker.readiness()
        return {"service": "ok", "api_version": "1", "runtime_ready": not reasons,
                "not_ready_reasons": reasons, "generation_tested": False,
                "diagnostic": worker.backend.diagnostics()}

    @app.get("/api/v1/capabilities")
    def capabilities():
        reasons = worker.readiness()
        return {"backend": "diffusers-minimax-h3-t2va", "runtime_ready": not reasons,
                "not_ready_reasons": reasons, "generation_tested": False,
                "source_support": {"lora": "supported", "sparse_attention": "unsupported"},
                "adapter_capabilities": asdict(worker.backend.capabilities),
                "diagnostic": worker.backend.diagnostics(),
                "profiles": H3_PROFILES, "fps": 24, "frames": {"min": 124, "max": 345, "stride": 17, "offset": 5},
                "service_limits": {"axis_min": 32, "axis_max": 1344, "spatial_multiple": 32,
                                   "canvas_pixels": 1344 * 768, "steps_min": 2, "steps_max": 1000,
                                   "pending_jobs": worker.queue_limit, "max_output_bytes": worker.max_output_bytes}}

    @app.post("/api/v1/jobs", status_code=202)
    def submit(payload: GenerationInput):
        try:
            job = worker.submit(payload.to_request())
        except ValueError:
            raise HTTPException(422, "unsupported_request") from None
        except BackendUnavailable:
            raise HTTPException(503, "backend_not_ready") from None
        except QueueFull:
            raise HTTPException(429, "queue_full", headers={"Retry-After": "5"}) from None
        except OSError:
            raise HTTPException(507, "insufficient_storage") from None
        return _job_data(store, job)

    @app.get("/api/v1/jobs")
    def jobs(limit: Annotated[int, Query(ge=1, le=1000)] = 100,
             offset: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0):
        rows = store.list_jobs(limit=limit, offset=offset)
        return {"jobs": [_job_data(store, job) for job in rows], "next_offset": offset + len(rows)}

    JobID = Annotated[str, APIPath(pattern=r"^[0-9a-f]{32}$")]

    @app.get("/api/v1/jobs/{job_id}")
    def detail(job_id: JobID):
        try:
            return _job_data(store, store.get_job(job_id))
        except KeyError:
            raise HTTPException(404, "job_not_found") from None

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel(job_id: JobID):
        try:
            return _job_data(store, worker.cancel(job_id))
        except KeyError:
            raise HTTPException(404, "job_not_found") from None

    @app.get("/api/v1/events", response_class=StreamingResponse)
    async def events(request: Request, after: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
                     follow: bool = True, last_event_id: Annotated[str | None, Header()] = None):
        if last_event_id is not None:
            if not re.fullmatch(r"[0-9]{1,19}", last_event_id):
                raise HTTPException(422, "invalid_event_cursor")
            after = int(last_event_id)
        try:
            store.read_events(after=after, limit=1)
        except ReplayGap:
            pass
        except ValueError:
            raise HTTPException(422, "invalid_event_cursor") from None

        async def stream():
            cursor = after
            last_emit = asyncio.get_running_loop().time()
            while not await request.is_disconnected():
                try:
                    page = store.read_events(after=cursor, limit=100)
                except ReplayGap as gap:
                    cursor = gap.minimum_cursor
                    yield "event: reset\ndata: " + json.dumps({"minimum_cursor": cursor, "refresh": "/api/v1/jobs"}, separators=(",", ":")) + "\n\n"
                    continue
                for record in page.events:
                    cursor = record.event_id
                    yield f"id: {cursor}\nevent: job\ndata: " + json.dumps(asdict(record), separators=(",", ":")) + "\n\n"
                    last_emit = asyncio.get_running_loop().time()
                if page.has_more:
                    continue
                if not follow:
                    return
                if asyncio.get_running_loop().time() - last_emit >= heartbeat_seconds:
                    yield ": heartbeat\n\n"
                    last_emit = asyncio.get_running_loop().time()
                await asyncio.sleep(min(.25, heartbeat_seconds))

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/api/v1/jobs/{job_id}/video", response_class=StreamingResponse)
    def video(job_id: JobID, download: bool = False, range_header: Annotated[str | None, Header(alias="Range")] = None):
        try:
            fd = worker.open_video(job_id)
        except KeyError:
            raise HTTPException(404, "job_not_found") from None
        except (OSError, ValueError):
            raise HTTPException(410, "output_unavailable_or_expired") from None
        size = os.fstat(fd).st_size
        try:
            start, end, status_code = _range(range_header, size)
        except ValueError:
            os.close(fd)
            raise HTTPException(416, "invalid_range", headers={"Content-Range": f"bytes */{size}"}) from None
        stream = os.fdopen(fd, "rb")
        stream.seek(start)
        length = end - start + 1

        def chunks():
            remaining = length
            try:
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        return
                    remaining -= len(chunk)
                    yield chunk
            finally:
                stream.close()

        headers = {"Accept-Ranges": "bytes", "Content-Length": str(length),
                   "Content-Disposition": f'{"attachment" if download else "inline"}; filename="{job_id}.mp4"'}
        if status_code == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return StreamingResponse(chunks(), status_code=status_code, media_type="video/mp4", headers=headers,
                                 background=BackgroundTask(stream.close))

    # The generated schema documents the same auth required by the middleware.
    original_openapi = app.openapi
    def openapi():
        schema = original_openapi()
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["RemoteToken"] = {"type": "http", "scheme": "bearer"}
        schema["security"] = [{"RemoteToken": []}]
        return schema
    app.openapi = openapi
    return app
