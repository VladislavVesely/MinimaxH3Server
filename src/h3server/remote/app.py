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

from h3server.domain import GenerationSettings, H3_PROFILES, JobRequest, canvas_limits
from h3server.backends.catalog import PRUNED_PROFILE_NAMES
from h3server.events import ReplayGap
from h3server.paths import AppPaths
from h3server.remote.worker import BackendUnavailable, QueueFull, Worker, _directory
from h3server.store import Store


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class MaintenanceInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    operation: str = Field(pattern=r'^[a-f0-9]{32}$')
    policy: Literal['reject', 'drain', 'cancel'] = 'reject'
    cancel_confirmed: StrictBool = False


class TurboInstallInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    artifact_digest: str = Field(pattern=r'^[a-f0-9]{64}$')


class ModelInstallInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    profile: str
    artifact_set_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    license_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    license_acknowledged: Literal[True]
    eligibility_confirmed: Literal[True]
    download_approved: Literal[True]


class RuntimeArtifactInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    release_id: str = Field(pattern=r'^[A-Za-z0-9_-]+$', max_length=128)
    filename: str = Field(pattern=r'^h3server-[A-Za-z0-9_.+-]+-py3-none-any\.whl$', max_length=200)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class ComponentSyncInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['synchronize']
    runtime_artifact: RuntimeArtifactInput | None = None
    desired: list[Annotated[str, Field(min_length=1,max_length=64)]] = Field(max_length=16)
    inventory_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    license_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    license_acknowledged: Literal[True]
    eligibility_confirmed: Literal[True]
    download_approved: Literal[True]


class ModelCancelInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation: str = Field(pattern=r'^[a-f0-9]{32}$')


class GenerationInput(BaseModel):
    """Canvas area<=1,032,192 is service policy, not an upstream hard maximum.

    Frames use the nearest supported 17*n+5 alignment; 15 seconds maps to 345 frames.
    Steps map to the runtime sampler (legacy default 50, pruned default 20).
    Denoising progress reports actual stage-local work.
    """
    model_config = ConfigDict(extra="forbid", strict=True)
    prompt: Annotated[StrictStr, Field(min_length=1, max_length=16000)]
    num_frames: Annotated[StrictInt | None, Field(ge=120, le=345)] = None
    duration: Annotated[StrictFloat | StrictInt | None, Field(ge=5, le=15)] = None
    width: Annotated[StrictInt, Field(ge=32, le=1920)] = 1344
    height: Annotated[StrictInt, Field(ge=32, le=1920)] = 768
    steps: Annotated[StrictInt, Field(ge=2, le=1000)] = 50
    seed: Annotated[StrictInt, Field(ge=0, le=2**63 - 1)] = 42
    profile: Literal["h3-bf16-cpu-offload-experimental", "h3-int8-group-offload-experimental",
                     "h3-gguf-q4-k-m", "h3-gguf-q5-k-m", "h3-convrot-mixed", "h3-convrot-int8", "h3-nvfp4"] = H3_PROFILES[0]
    loras: Annotated[list[StrictStr], Field(max_length=8)] = Field(default_factory=list)
    sparse_attention: StrictBool | None = None
    sage_attention: StrictBool = False
    turbo_lora: StrictBool = False
    turbo_strength: Annotated[float, Field(strict=True, ge=.8, le=1.2)] = 1.0
    turbo_low_vram: StrictBool = False
    turbo_artifact_digest: Annotated[StrictStr, Field(pattern=r'^[a-f0-9]{64}$')] | None = None
    sparse_preset: Literal['off', 'quality', 'balanced', 'draft'] = 'off'
    sparse_artifact_digest: Annotated[StrictStr, Field(pattern=r'^[a-f0-9]{64}$')] | None = None

    input_mode: Literal['text', 'frames', 'references'] = 'text'
    reference_images: Annotated[list[StrictStr], Field(max_length=9)] = Field(default_factory=list)
    reference_videos: Annotated[list[StrictStr], Field(max_length=3)] = Field(default_factory=list)
    reference_audios: Annotated[list[StrictStr], Field(max_length=3)] = Field(default_factory=list)
    reference_artifact_digest: Annotated[StrictStr, Field(pattern=r'^[a-f0-9]{64}$')] | None = None
    first_frame: StrictStr | None = None
    last_frame: StrictStr | None = None

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
        from h3server.domain import aligned_frames
        aligned = aligned_frames(frames)
        steps = (6 if self.turbo_lora else 20) if self.profile in PRUNED_PROFILE_NAMES and 'steps' not in self.model_fields_set else self.steps
        settings = GenerationSettings(aligned, self.width, self.height, steps, self.seed, self.profile, self.sage_attention,
                                      self.turbo_lora, self.turbo_strength, self.turbo_low_vram, self.turbo_artifact_digest,
                                      self.sparse_preset, self.sparse_artifact_digest,
                                      self.input_mode, tuple(self.reference_images), tuple(self.reference_videos), tuple(self.reference_audios), self.first_frame, self.last_frame, self.reference_artifact_digest)
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


def _job_data(store, job, worker=None):
    import time
    job, output = store.get_job_snapshot(job.job_id)
    value = asdict(job)
    if value['timing'] is not None:
        value['timing']['observed_at'] = time.time()
    if job.request.settings is not None:
        value["effective_duration"] = job.request.settings.duration
        s = job.request.settings
        value['effective_geometry'] = dict(width=s.width, height=s.height, num_frames=s.num_frames,
                                           fps=24, duration=s.duration)
    value["output"] = asdict(output) if output else None
    if worker is not None: value['estimate'] = worker.job_estimate(job)
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

    from h3server.remote.model_library import ModelLibrary
    library = ModelLibrary(worker)
    from h3server.remote.features import SageInstaller
    features = SageInstaller(worker)
    from h3server.remote.turbo import TurboInstaller
    turbo = TurboInstaller(worker)
    from h3server.remote.reference import ReferenceInstaller
    reference = ReferenceInstaller(worker)
    from h3server.remote.sparse import SparseInstaller
    sparse = SparseInstaller(worker)
    from h3server.remote.components import Components
    components = Components(worker,library,{'sageattention':features,'turbo':turbo,'sparse':sparse,'reference':reference})

    def library_document():
        return {**library.status(),'components':components.inventory(),'synchronization':components.status()}

    def optional_document(installer,name):
        state=installer.status()
        profiles=library.optional_profiles(name) if library.supported else {}
        return {**state,'by_profile':profiles,'installed':any(profiles.values()) if profiles else state['installed']}

    @asynccontextmanager
    async def lifespan(app):
        worker.start()
        components.recover()
        library.recover()
        features.recover()
        turbo.recover()
        reference.recover()
        sparse.recover()
        try:
            yield
        finally:
            await asyncio.to_thread(components.close)
            await asyncio.to_thread(library.close)
            await asyncio.to_thread(features.close)
            await asyncio.to_thread(turbo.close)
            await asyncio.to_thread(reference.close)
            await asyncio.to_thread(sparse.close)
            await asyncio.to_thread(worker.stop)

    app = FastAPI(title="MiniMax H3 remote API", version="1.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.state.request_model = GenerationInput
    app.state.model_library = library
    app.state.components = components

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

    @app.get('/api/v1/models/library')
    def model_library():return library_document()

    @app.post('/api/v1/models/install',status_code=202)
    def install_model(body: ModelInstallInput | ComponentSyncInput):
        try:
            if isinstance(body,ComponentSyncInput):return components.start(**body.model_dump(exclude={'action'}))
            return library.start(**body.model_dump())
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @app.post('/api/v1/models/install/cancel')
    def cancel_model_install(body: ModelCancelInput):
        try:
            if components._read().get('operation')==body.operation:return components.cancel(body.operation)
            return library.cancel(body.operation)
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @app.get('/api/v1/features/sageattention')
    def sage_status():
        return features.status()

    @app.post('/api/v1/features/sageattention', status_code=202)
    def install_sage():
        try:
            return features.start()
        except ValueError:
            raise HTTPException(409, 'optional_install_busy_or_unsupported') from None

    @app.get('/api/v1/features/turbo')
    def turbo_status():
        return turbo.status()

    @app.post('/api/v1/features/turbo', status_code=202)
    def install_turbo(body: TurboInstallInput):
        from h3server.backends.turbo import DIGEST
        if body.artifact_digest != DIGEST:
            raise HTTPException(409, 'turbo_selection_changed')
        try:
            return turbo.start(body.artifact_digest)
        except ValueError:
            raise HTTPException(409, 'optional_install_busy_or_unsupported') from None

    @app.get('/api/v1/features/reference')
    def reference_status():return reference.status()

    @app.post('/api/v1/features/reference',status_code=202)
    def install_reference(body: TurboInstallInput):
        from h3server.backends.reference import DIGEST
        if body.artifact_digest!=DIGEST:raise HTTPException(409,'reference_selection_changed')
        try:return reference.start(body.artifact_digest)
        except ValueError:raise HTTPException(409,'optional_install_busy_or_unsupported') from None

    @app.get('/api/v1/features/sparse')
    def sparse_status():
        return sparse.status()

    @app.post('/api/v1/features/sparse', status_code=202)
    def install_sparse(body: TurboInstallInput):
        from h3server.backends.sparse import DIGEST
        if body.artifact_digest != DIGEST:
            raise HTTPException(409, 'sparse_selection_changed')
        try:
            return sparse.start(body.artifact_digest)
        except ValueError:
            raise HTTPException(409, 'optional_install_busy_or_unsupported') from None

    from h3server.assets import AssetStore, MAX_BYTES
    assets = AssetStore(worker.paths)
    asset_upload_lock = asyncio.Lock()

    @app.post('/api/v1/assets/{kind}', status_code=201)
    async def upload_asset(kind: Literal['image', 'video', 'audio'], request: Request):
        if worker.maintenance_status().get('active'):raise HTTPException(409, 'maintenance_active')
        data=bytearray()
        try:
            async with asset_upload_lock, asyncio.timeout(90):
                async for chunk in request.stream():
                    if len(data)+len(chunk)>MAX_BYTES:raise HTTPException(413,'asset_size_limit_64_mib')
                    data.extend(chunk)
                return await asyncio.to_thread(assets.put, data, kind)
        except TimeoutError:raise HTTPException(408,'asset_upload_timeout') from None
        except (ValueError,OSError):raise HTTPException(422,'asset_invalid_or_limits_exceeded') from None

    AssetID = Annotated[str, APIPath(pattern=r'^[a-f0-9]{64}$')]

    @app.get('/api/v1/assets/{id}')
    def asset_metadata(id: AssetID):
        try:return assets.metadata(id)
        except (OSError,ValueError):raise HTTPException(404,'asset_missing_or_changed') from None

    @app.get('/api/v1/assets/{id}/content')
    def asset_content(id: AssetID, range_header: Annotated[str | None, Header(alias='Range')] = None):
        try:
            info=assets.metadata(id)
            with assets.open(id) as original:fd=os.dup(original.fileno())
        except (OSError,ValueError):raise HTTPException(404,'asset_missing_or_changed') from None
        size=os.fstat(fd).st_size
        try:start,end,code=_range(range_header,size)
        except ValueError:
            os.close(fd);raise HTTPException(416,'invalid_range',headers={'Content-Range':f'bytes */{size}'}) from None
        stream=os.fdopen(fd,'rb');stream.seek(start)
        def chunks():
            remaining=end-start+1
            try:
                while remaining:
                    chunk=stream.read(min(65536,remaining))
                    if not chunk:return
                    remaining-=len(chunk);yield chunk
            finally:stream.close()
        headers={'Content-Length':str(end-start+1),'Accept-Ranges':'bytes','Content-Disposition':'inline; filename="reference-asset"'}
        if code==206:headers['Content-Range']=f'bytes {start}-{end}/{size}'
        return StreamingResponse(chunks(),status_code=code,media_type=info['mime'],headers=headers,background=BackgroundTask(stream.close))

    @app.get('/api/v1/models')
    def models_status():
        return worker.models_status()

    @app.post('/api/v1/models/unload')
    def unload_models(payload: EmptyInput):
        try: return worker.unload_models()
        except ValueError:
            raise HTTPException(409, 'models_busy_wait_for_queue_and_maintenance') from None

    @app.post('/api/v1/performance/estimate')
    def estimate(payload: GenerationInput):
        try: return worker.estimate(payload.to_request())
        except ValueError:
            raise HTTPException(422, 'unsupported_request') from None

    @app.get('/api/v1/performance')
    def performance(offset: Annotated[int, Query(ge=0, le=2**31-1)] = 0):
        return worker.performance.export(offset=offset)

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
        return {"backend": worker.backend.runtime_id, "runtime_ready": not reasons,
                "not_ready_reasons": reasons, "generation_tested": False,
                "source_support": {name: getattr(worker.backend.capabilities, name).status.value
                                   for name in ('lora', 'sparse_attention')},
                "adapter_capabilities": asdict(worker.backend.capabilities),
                "diagnostic": worker.backend.diagnostics(),
                "resident_models": worker.models_status(), "model_library": library_document(), "performance_schema": 1,
                "input_modes": ['text', 'frames', 'references'] if 'h3-convrot-int8' in worker.backend.profiles else ['text', 'frames'] if any(p in PRUNED_PROFILE_NAMES for p in worker.backend.profiles) else ['text'],
                "optional_features": {"reference": reference.status(), "sage_attention": optional_document(features,'sageattention'), "turbo_lora": turbo.status(), "sparse_attention": optional_document(sparse,'sparse')},
                "profiles": worker.backend.profiles, "fps": 24, "frames": {"min": 124, "max": 345, "stride": 17, "offset": 5},
                "service_limits": {"axis_min": 32, "axis_max": canvas_limits(worker.backend.profiles[0])[0], "spatial_multiple": 32,
                                   "canvas_pixels": canvas_limits(worker.backend.profiles[0])[1], "steps_min": 2, "steps_max": 1000,
                                   "steps_default": 20 if worker.backend.runtime_id.startswith('comfyui-h3-') else 50,
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
        except FileNotFoundError:
            raise HTTPException(422, 'required_generation_file_missing') from None
        except OSError:
            raise HTTPException(507, "insufficient_storage") from None
        return _job_data(store, job, worker)

    @app.get("/api/v1/jobs")
    def jobs(limit: Annotated[int, Query(ge=1, le=1000)] = 100,
             offset: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0):
        rows = store.list_jobs(limit=limit, offset=offset)
        return {"jobs": [_job_data(store, job, worker) for job in rows], "next_offset": offset + len(rows)}

    JobID = Annotated[str, APIPath(pattern=r"^[0-9a-f]{32}$")]

    @app.get("/api/v1/jobs/{job_id}")
    def detail(job_id: JobID):
        try:
            return _job_data(store, store.get_job(job_id), worker)
        except KeyError:
            raise HTTPException(404, "job_not_found") from None

    @app.post('/api/v1/jobs/{job_id}/delete')
    def delete_job(job_id: JobID):
        try:return worker.delete(job_id)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        except OSError:raise HTTPException(409,'Cleanup could not finish safely. The job remains listed; resolve the storage problem and retry deletion.') from None

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel(job_id: JobID):
        try:
            return _job_data(store, worker.cancel(job_id), worker)
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

    from threading import Lock
    frame_export_lock = Lock()
    class FrameExportBusy(Exception):pass

    @app.get('/api/v1/jobs/{job_id}/last-frame')
    async def last_frame(job_id: JobID):
        from fastapi.responses import Response
        from h3server.last_frame import extract
        def export():
            if not frame_export_lock.acquire(blocking=False):raise FrameExportBusy()
            try:
                fd = worker.open_video(job_id)
                try:return extract(fd)
                finally:os.close(fd)
            finally:frame_export_lock.release()
        try:
            async with asyncio.timeout(30):
                data = await asyncio.to_thread(export)
        except FrameExportBusy:raise HTTPException(409,'last_frame_export_busy') from None
        except KeyError:raise HTTPException(404,'job_not_found') from None
        except TimeoutError:raise HTTPException(504,'last_frame_export_timeout') from None
        except ArithmeticError:raise HTTPException(422,'last_frame_precision_unsupported') from None
        except (OSError,ValueError):raise HTTPException(410,'last_frame_unavailable') from None
        return Response(data,media_type='image/png',headers={
            'Content-Disposition':f'attachment; filename="{job_id}-last-frame.png"',
            'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff'})

    audio_export_lock = Lock()

    @app.get('/api/v1/jobs/{job_id}/audio')
    async def audio_export(job_id: JobID):
        from fastapi.responses import Response
        from h3server.audio_export import extract, NoAudio
        def export():
            if not audio_export_lock.acquire(blocking=False):raise FrameExportBusy()
            try:
                fd = worker.open_video(job_id)
                try:return extract(fd)
                finally:os.close(fd)
            finally:audio_export_lock.release()
        try:
            async with asyncio.timeout(30):data = await asyncio.to_thread(export)
        except FrameExportBusy:raise HTTPException(409,'audio_export_busy') from None
        except KeyError:raise HTTPException(404,'job_not_found') from None
        except NoAudio:raise HTTPException(422,'audio_track_missing') from None
        except TimeoutError:raise HTTPException(504,'audio_export_timeout') from None
        except (OSError,ValueError):raise HTTPException(410,'audio_export_unavailable') from None
        return Response(data,media_type='audio/mpeg',headers={
            'Content-Disposition':f'attachment; filename="{job_id}.mp3"',
            'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

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
