"""Backend-independent facts and requests, without hardware-fit assumptions."""

from dataclasses import dataclass, field
from enum import StrEnum
import math


def _nonnegative(value: int | float, name: str) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")


@dataclass(frozen=True)
class GPUInfo:
    name: str | None = None
    vram_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.vram_bytes is not None:
            _nonnegative(self.vram_bytes, "vram_bytes")


@dataclass(frozen=True)
class HardwareInfo:
    """None means unmeasured; gpus contains observed devices, not necessarily all.

    An empty GPU tuple proves absence only with gpu_inventory_complete=True.
    False means the inventory is not known complete, including hand-built facts.
    """

    distribution: str | None = None
    distribution_version: str | None = None
    architecture: str | None = None
    cpu: str | None = None
    cpu_count: int | None = None
    ram_bytes: int | None = None
    gpus: tuple[GPUInfo, ...] | None = None
    driver_version: str | None = None
    cuda_available: bool | None = None
    disk_free_bytes: int | None = None
    service_manager: str | None = None
    evidence: tuple[str, ...] = ()
    gpu_inventory_complete: bool = False

    def __post_init__(self) -> None:
        if type(self.gpu_inventory_complete) is not bool:
            raise ValueError("gpu_inventory_complete must be bool")
        if self.gpu_inventory_complete and self.gpus is None:
            raise ValueError("a complete GPU inventory requires observed devices or measured absence")
        for name in ("cpu_count", "ram_bytes", "disk_free_bytes"):
            value = getattr(self, name)
            if value is not None:
                _nonnegative(value, name)


class CapabilityStatus(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Capability:
    status: CapabilityStatus
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", CapabilityStatus(self.status))
        if (
            not isinstance(self.evidence, tuple)
            or not self.evidence
            or any(not isinstance(item, str) or not item.strip() for item in self.evidence)
        ):
            raise ValueError("capability evidence must be a tuple of nonempty observations")


def _unknown_capability() -> Capability:
    return Capability(CapabilityStatus.UNKNOWN, ("No backend evidence collected",))


@dataclass(frozen=True)
class Capabilities:
    lora: Capability = field(default_factory=_unknown_capability)
    sparse_attention: Capability = field(default_factory=_unknown_capability)


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Progress:
    """Measured work only: status never supplies a synthetic percentage."""

    completed: int | float
    unit: str
    total: int | float | None = None

    def __post_init__(self) -> None:
        _nonnegative(self.completed, "completed")
        if self.total is not None:
            _nonnegative(self.total, "total")
            if self.completed > self.total:
                raise ValueError("completed must not exceed total (percent must be 0..100)")
        if not self.unit.strip():
            raise ValueError("progress unit must not be empty")

    @property
    def percent(self) -> float | None:
        # A zero total also has no defined ratio; it is not evidence of completion.
        if self.total is None or self.total == 0:
            return None
        return self.completed / self.total * 100


@dataclass(frozen=True)
class Event:
    stage: str
    status: StageStatus
    message: str = ""
    progress: Progress | None = None


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


H3_PROFILES = (
    "h3-bf16-cpu-offload-experimental",
    "h3-int8-group-offload-experimental",
)


TURBO_PROFILES = ('h3-convrot-int8', 'h3-convrot-mixed')


def canvas_limits(profile):
    """Service bounds, not a claim of benchmarked GPU memory or image quality."""
    from h3server.backends.catalog import PRUNED_PROFILE_NAMES
    return (1920, 1920 * 1088) if profile in PRUNED_PROFILE_NAMES else (1344, 1344 * 768)


@dataclass(frozen=True)
class GenerationSettings:
    """Normalized H3 settings. Canvas/step caps are service resource policy.

    Both orientations fit the selected profile's service area; these are not
    assertions of upstream explicit-input maxima or GPU memory sufficiency. Steps map to the selected runtime's sampler;
    progress reports actual stage-local work rather than assuming model calls.
    """

    num_frames: int = 124
    width: int = 1344
    height: int = 768
    steps: int = 50
    seed: int = 42
    profile: str = H3_PROFILES[0]
    sage_attention: bool = False
    turbo_lora: bool = False
    turbo_strength: float = 1.0
    turbo_low_vram: bool = False
    turbo_artifact_digest: str | None = None
    sparse_preset: str = 'off'
    sparse_artifact_digest: str | None = None
    input_mode: str = 'text'
    reference_images: tuple[str, ...] = ()
    reference_videos: tuple[str, ...] = ()
    reference_audios: tuple[str, ...] = ()
    first_frame: str | None = None
    last_frame: str | None = None
    reference_artifact_digest: str | None = None

    def __post_init__(self):
        import re
        from h3server.backends.catalog import PRUNED_PROFILE_NAMES
        if self.input_mode not in ('text', 'frames', 'references'):
            raise ValueError('Unknown input mode')
        for name, maximum in (('reference_images',9),('reference_videos',3),('reference_audios',3)):
            values = getattr(self, name)
            if type(values) not in (tuple, list) or len(values) > maximum:
                raise ValueError('Invalid reference count')
            object.__setattr__(self, name, tuple(values))
        ids = (*self.reference_images, *self.reference_videos, *self.reference_audios)
        for value in (*ids, self.first_frame, self.last_frame):
            if value is not None and (type(value) is not str or not re.fullmatch(r'[a-f0-9]{64}', value)):
                raise ValueError('Asset IDs must be SHA256 identifiers, not paths or URLs')
        if any(value is None for value in ids):
            raise ValueError('Reference IDs cannot be null')
        frames = bool(self.first_frame or self.last_frame)
        if ((self.input_mode == 'text' and (ids or frames)) or
            (self.input_mode == 'frames' and (not frames or ids)) or
            (self.input_mode == 'references' and (not ids or frames))):
            raise ValueError('Assets do not match input mode')
        if self.input_mode != 'text':
            if self.profile not in PRUNED_PROFILE_NAMES:
                raise ValueError('Conditioned inputs require a pruned runtime profile')
            if self.turbo_lora or self.sparse_preset != 'off':
                raise ValueError('Turbo and sparse are not verified with conditioned inputs; disable them')
        if self.input_mode == 'references' and self.reference_artifact_digest is None:
            from h3server.backends.reference import DIGEST
            object.__setattr__(self, 'reference_artifact_digest', DIGEST)
        if self.reference_artifact_digest is not None and (type(self.reference_artifact_digest) is not str or not re.fullmatch(r'[a-f0-9]{64}', self.reference_artifact_digest)):
            raise ValueError('Invalid Ref2VA artifact digest')
        if self.input_mode != 'references' and self.reference_artifact_digest is not None:
            raise ValueError('Ref2VA digest requires reference input mode')
        if self.input_mode == 'references' and self.profile != 'h3-convrot-int8':
            raise ValueError('Ref2VA currently requires ConvRot INT8 and the optional reference checkpoint')
        if self.sparse_preset not in ('off', 'quality', 'balanced', 'draft'):
            raise ValueError('Unknown sparse attention preset')
        if self.sparse_preset != 'off':
            if self.profile != 'h3-convrot-int8' or self.turbo_lora:
                raise ValueError('Sparse attention requires ConvRot INT8 with Turbo disabled')
            if self.sparse_artifact_digest is None:
                from h3server.backends.sparse import DIGEST
                object.__setattr__(self, 'sparse_artifact_digest', DIGEST)
        if self.sparse_artifact_digest is not None and (not isinstance(self.sparse_artifact_digest, str) or len(self.sparse_artifact_digest) != 64 or any(c not in '0123456789abcdef' for c in self.sparse_artifact_digest)):
            raise ValueError('Invalid sparse artifact digest')
        if type(self.turbo_lora) is not bool or type(self.turbo_low_vram) is not bool:
            raise ValueError('Turbo selections must be bool')
        if type(self.turbo_strength) not in (int, float) or not math.isfinite(self.turbo_strength) or not .8 <= self.turbo_strength <= 1.2:
            raise ValueError('Turbo strength must be between 0.8 and 1.2')
        if self.turbo_lora and (self.profile not in TURBO_PROFILES or type(self.steps) is not int or not 4 <= self.steps <= 8):
            raise ValueError('Turbo requires a supported ConvRot profile and 4–8 steps')
        if self.turbo_lora and self.turbo_artifact_digest is None:
            from h3server.backends.turbo import DIGEST
            object.__setattr__(self, 'turbo_artifact_digest', DIGEST)
        if self.turbo_artifact_digest is not None and (not isinstance(self.turbo_artifact_digest, str) or len(self.turbo_artifact_digest) != 64 or any(c not in '0123456789abcdef' for c in self.turbo_artifact_digest)):
            raise ValueError('invalid Turbo artifact digest')
        if type(self.sage_attention) is not bool:
            raise ValueError('sage_attention must be bool')
        for name in ("num_frames", "width", "height", "steps", "seed"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} must be an integer")
        if not 124 <= self.num_frames <= 345 or (self.num_frames - 5) % 17:
            raise ValueError("num_frames must be aligned to 17*n+5 within 124..345")
        axis_max, canvas_pixels = canvas_limits(self.profile)
        if any(not 32 <= side <= axis_max or side % 32 for side in (self.width, self.height)):
            raise ValueError(f"canvas axes must be multiples of 32 within 32..{axis_max}")
        if self.width * self.height > canvas_pixels:
            raise ValueError("canvas exceeds service pixel-area limit")
        if not 2 <= self.steps <= 1000 or not 0 <= self.seed <= 2**63 - 1:
            raise ValueError("steps or seed outside service bounds")
        # Lazy catalog lookup avoids domain/catalog import cycles and preserves
        # the legacy defaults; an explicit selected profile never falls back.
        from h3server.backends import get_profile
        get_profile(self.profile)

    @property
    def duration(self) -> float:
        return self.num_frames / 24


def aligned_frames(frames):
    """Nearest supported 17*n+5 frame count, clamped to the service range."""
    import math
    if type(frames) not in (int, float) or not math.isfinite(frames) or frames <= 0:
        raise ValueError('invalid frame count')
    return 5 + 17 * min(20, max(7, math.floor((frames - 5) / 17 + .5)))


@dataclass(frozen=True)
class JobRequest:
    """Validate against the selected backend before accepting or executing work.

    LoRA values are logical adapter identifiers, not trusted filesystem paths.
    Sparse attention None means omitted; even explicit False needs support.
    """

    prompt: str
    loras: tuple[str, ...] = ()
    sparse_attention: bool | None = None
    settings: GenerationSettings | None = None

    def __post_init__(self) -> None:
        if self.settings is not None and not isinstance(self.settings, GenerationSettings):
            raise ValueError("settings must be normalized GenerationSettings")
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("prompt must not be empty")
        if not isinstance(self.loras, tuple) or any(
            not isinstance(identifier, str) or not identifier.strip() for identifier in self.loras
        ):
            raise ValueError("LoRA identifiers must be a tuple of nonempty strings")
        if self.sparse_attention is not None and type(self.sparse_attention) is not bool:
            raise ValueError("sparse_attention must be bool or None")

    def validate_capabilities(self, capabilities: Capabilities) -> None:
        for feature, requested in (
            ("lora", bool(self.loras)),
            ("sparse_attention", self.sparse_attention is not None),
        ):
            capability = getattr(capabilities, feature)
            if requested and capability.status is not CapabilityStatus.SUPPORTED:
                raise ValueError(
                    f"{feature} is {capability.status.value}: {'; '.join(capability.evidence)}"
                )


@dataclass(frozen=True)
class JobTiming:
    """UTC lifecycle timestamps for the current attempt, not video duration."""
    queued_at: float | None = None
    started_at: float | None = None
    finished_at: float | None = None

    def __post_init__(self):
        for name in ('queued_at', 'started_at', 'finished_at'):
            value = getattr(self, name)
            if value is not None:
                _nonnegative(value, name)


@dataclass(frozen=True)
class Job:
    job_id: str
    request: JobRequest
    status: JobStatus = JobStatus.QUEUED
    timing: JobTiming | None = None
