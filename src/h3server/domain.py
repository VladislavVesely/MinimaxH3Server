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
    """None means unmeasured; an empty GPU tuple means measured absence."""

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

    def __post_init__(self) -> None:
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


@dataclass(frozen=True)
class JobRequest:
    """Validate against the selected backend before accepting or executing work.

    LoRA values are logical adapter identifiers, not trusted filesystem paths.
    Sparse attention None means omitted; even explicit False needs support.
    """

    prompt: str
    loras: tuple[str, ...] = ()
    sparse_attention: bool | None = None

    def __post_init__(self) -> None:
        if not self.prompt.strip():
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
class Job:
    job_id: str
    request: JobRequest
    status: JobStatus = JobStatus.QUEUED
