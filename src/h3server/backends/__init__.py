"""Packaged source evidence, not an inference adapter or readiness assertion."""

from importlib.resources import files
import json
from typing import Any

from h3server.profiles import DiskEstimate, Profile
from h3server.backends.catalog import get_artifact_set, pruned_profiles


def load_manifest() -> dict[str, Any]:
    """Read a fresh copy of the bundled manifest; never fetch or execute anything."""
    return json.loads(files(__package__).joinpath("manifest.json").read_text(encoding="utf-8"))


def load_profiles(*, include_pruned: bool = False) -> tuple[Profile, ...]:
    """Legacy defaults stay unchanged; opt-in lists smaller pruned choices first."""
    manifest = load_manifest()
    legacy = tuple(
        Profile(
            name=item["name"],
            status=item["status"],
            evidence=tuple(item["evidence"]),
            min_gpu_count=item["min_gpu_count"],
            min_gpu_vram_bytes=item["min_gpu_vram_bytes"],
            min_ram_bytes=item["min_ram_bytes"],
            requires_cuda_runtime=item["requires_cuda_runtime"],
            backend_revision=manifest["backend"]["revision"],
            disk=DiskEstimate(item["disk"]["required_bytes"], tuple(item["disk"]["evidence"])),
        )
        for item in manifest["profiles"]
    )
    return pruned_profiles() + legacy if include_pruned else legacy


def get_profile(profile_name: str) -> Profile:
    """Exact lookup only; unknown names never fall back to legacy weights."""
    for profile in load_profiles(include_pruned=True):
        if profile.name == profile_name:
            return profile
    get_artifact_set(profile_name)  # supplies the unavailable/unknown explanation
    raise ValueError("unknown profile")
