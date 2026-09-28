"""Explain fits against supplied evidence, without built-in H3 size guarantees.

Task 3's pinned backend research must supply profiles. VERIFIED describes the
supplied profile's evidence, not a generation test on the scanned machine.
Unknown observations or incomplete criteria always prevent that recommendation.
"""

from dataclasses import dataclass
from enum import StrEnum

from h3server.domain import HardwareInfo


class RecommendationStatus(StrEnum):
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"
    EXPERIMENTAL = "experimental"
    VERIFIED = "verified"


def _evidence(values: tuple[str, ...], name: str) -> None:
    if not isinstance(values, tuple) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError(f"{name} must be a nonempty tuple of provenance strings")


def _quantity(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


@dataclass(frozen=True)
class DiskEstimate:
    """Required free bytes, including the profile author's staging/cache budget."""

    required_bytes: int
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        _quantity(self.required_bytes, "required_bytes")
        _evidence(self.evidence, "disk evidence")


@dataclass(frozen=True)
class Profile:
    """Explicit requirements, not defaults. Empty/None criteria remain unknown.

    Distribution entries are exact (ID, VERSION_ID) pairs, not distro families.
    GPU memory is per device; count=0 explicitly permits CPU-only evaluation.
    Unlisted GPU models, distro versions and drivers lack verification evidence;
    they are not automatically asserted to be incompatible.
    """

    name: str
    evidence: tuple[str, ...]
    status: RecommendationStatus = RecommendationStatus.UNKNOWN
    distributions: tuple[tuple[str, str], ...] = ()
    architectures: tuple[str, ...] = ()
    gpu_names: tuple[str, ...] = ()
    min_gpu_count: int | None = None
    min_gpu_vram_bytes: int | None = None
    min_ram_bytes: int | None = None
    disk: DiskEstimate | None = None
    driver_versions: tuple[str, ...] = ()
    service_managers: tuple[str, ...] = ()
    requires_cuda_runtime: bool | None = None
    backend_revision: str | None = None
    verification_evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("profile name must not be empty")
        object.__setattr__(self, "status", RecommendationStatus(self.status))
        _evidence(self.evidence, "profile evidence")
        for name in ("min_gpu_count", "min_gpu_vram_bytes", "min_ram_bytes"):
            value = getattr(self, name)
            if value is not None:
                _quantity(value, name)
        if self.requires_cuda_runtime is not None and type(self.requires_cuda_runtime) is not bool:
            raise ValueError("requires_cuda_runtime must be bool or None")
        for name in ("architectures", "gpu_names", "driver_versions", "service_managers"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError(f"{name} must be a tuple of nonempty strings")
        if not isinstance(self.distributions, tuple) or any(
            not isinstance(pair, tuple) or len(pair) != 2
            or any(not isinstance(v, str) or not v.strip() for v in pair)
            for pair in self.distributions
        ):
            raise ValueError("distributions must contain exact (ID, VERSION_ID) pairs")
        if self.disk is not None and not isinstance(self.disk, DiskEstimate):
            raise ValueError("disk must be a provenance-bearing DiskEstimate")
        if self.min_gpu_count == 0 and self.min_gpu_vram_bytes not in (None, 0):
            raise ValueError("a CPU-only profile cannot require GPU memory")
        if self.backend_revision is not None and (
            not isinstance(self.backend_revision, str) or not self.backend_revision.strip()
        ):
            raise ValueError("backend_revision must be a nonempty string or None")
        if self.verification_evidence != ():
            _evidence(self.verification_evidence, "generation verification evidence")
        if self.status is RecommendationStatus.VERIFIED:
            if self.backend_revision is None:
                raise ValueError("verified profiles require a pinned backend revision")
            _evidence(self.verification_evidence, "generation verification evidence")


@dataclass(frozen=True)
class Recommendation:
    profile_name: str
    status: RecommendationStatus
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]
    disk: DiskEstimate | None


def evaluate_profile(hardware: HardwareInfo, profile: Profile) -> Recommendation:
    """Known requirement failures outrank unknowns; unknowns outrank confidence."""
    failures: list[str] = []
    unknowns: list[str] = []
    matches: list[str] = []

    def minimum(label: str, measured: int | None, required: int | None) -> None:
        if required is None:
            unknowns.append(f"{label}: no requirement supplied")
        elif measured is None:
            unknowns.append(f"{label}: not measured")
        elif measured < required:
            failures.append(f"{label}: {measured} is below required {required}")
        else:
            matches.append(f"{label}: {measured} meets supplied minimum {required}")

    def allowed(label: str, measured: str | None, values: tuple[str, ...], *, unlisted_unknown: bool = False) -> None:
        if not values or measured is None:
            unknowns.append(f"{label}: observation or profile evidence missing")
        elif measured not in values:
            (unknowns if unlisted_unknown else failures).append(f"{label}: {measured} is not listed in profile evidence")
        else:
            matches.append(f"{label}: {measured} matches profile evidence")

    if hardware.distribution is None:
        unknowns.append("distribution: not measured")
    elif hardware.distribution not in {"ubuntu", "debian"}:
        failures.append(f"distribution: {hardware.distribution} is outside the Ubuntu/Debian server scope")
    elif hardware.distribution_version is None or (hardware.distribution, hardware.distribution_version) not in profile.distributions:
        unknowns.append("distribution version: no exact version evidence; family identification is not certification")
    else:
        matches.append("distribution version: exact profile evidence matched")
    allowed("architecture", hardware.architecture, profile.architectures)
    allowed("service manager", hardware.service_manager, profile.service_managers)
    minimum("RAM bytes", hardware.ram_bytes, profile.min_ram_bytes)
    minimum("disk available bytes", hardware.disk_free_bytes, profile.disk.required_bytes if profile.disk else None)

    if profile.min_gpu_count is None:
        unknowns.append("GPU count: requirement missing")
    elif profile.min_gpu_count > 0:
        gpu_count = len(hardware.gpus) if hardware.gpus is not None else None
        if gpu_count is not None and gpu_count < profile.min_gpu_count and not hardware.gpu_inventory_complete:
            unknowns.append(f"GPU count: only {gpu_count} observed; inventory not known complete")
        else:
            minimum("GPU count", gpu_count, profile.min_gpu_count)
        if profile.min_gpu_vram_bytes is None:
            unknowns.append("VRAM per GPU: requirement missing")
        elif hardware.gpus is not None:
            enough = sum(gpu.vram_bytes is not None and gpu.vram_bytes >= profile.min_gpu_vram_bytes for gpu in hardware.gpus)
            unmeasured = sum(gpu.vram_bytes is None for gpu in hardware.gpus)
            if enough + unmeasured < profile.min_gpu_count and hardware.gpu_inventory_complete:
                failures.append(f"VRAM per GPU: fewer than {profile.min_gpu_count} devices meet {profile.min_gpu_vram_bytes} bytes; separate GPU memory is never pooled")
            elif enough < profile.min_gpu_count:
                unknowns.append("VRAM per GPU: " + (
                    "insufficient measurements" if hardware.gpu_inventory_complete
                    else "inventory not known complete; additional qualifying devices may be unobserved"
                ))
            else:
                matches.append("VRAM per GPU: enough individual devices meet supplied minimum")
        if not profile.gpu_names or hardware.gpus is None:
            unknowns.append("GPU identity: observation or profile evidence missing")
        elif any(gpu.name not in profile.gpu_names for gpu in hardware.gpus):
            unknowns.append("GPU identity: device not listed in profile evidence")
        else:
            matches.append("GPU identity: matches supplied profile evidence")

    if profile.requires_cuda_runtime is None:
        unknowns.append("CUDA runtime: requirement not specified")
    elif profile.requires_cuda_runtime:
        allowed("driver version", hardware.driver_version, profile.driver_versions, unlisted_unknown=True)
        if hardware.cuda_available is None:
            unknowns.append("CUDA runtime usability: unknown; driver compatibility/toolkit presence is not a runtime test")
        elif not hardware.cuda_available:
            failures.append("CUDA runtime: measured unusable")
        else:
            matches.append("CUDA runtime: reported usable by supplied hardware evidence")

    if profile.status is RecommendationStatus.UNSUPPORTED:
        failures.append("Profile evidence explicitly marks this profile unsupported")
    elif profile.status is RecommendationStatus.UNKNOWN:
        unknowns.append("Profile feasibility has not been established")
    status = (RecommendationStatus.UNSUPPORTED if failures else RecommendationStatus.UNKNOWN if unknowns else profile.status)
    explanation = "Evaluation compares supplied profile criteria only; not a guarantee of H3 fit or successful generation on this host."
    return Recommendation(profile.name, status, tuple(failures + unknowns + matches + [explanation]),
                          profile.evidence + profile.verification_evidence, profile.disk)


def recommend_profiles(hardware: HardwareInfo, profiles: tuple[Profile, ...] = ()) -> tuple[Recommendation, ...]:
    """No builtin inference profiles until pinned backend evidence is supplied."""
    return tuple(evaluate_profile(hardware, profile) for profile in profiles)
