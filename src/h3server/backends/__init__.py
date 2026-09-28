"""Packaged source evidence, not an inference adapter or readiness assertion."""

from importlib.resources import files
import json
from typing import Any

from h3server.profiles import DiskEstimate, Profile


def load_manifest() -> dict[str, Any]:
    """Read a fresh copy of the bundled manifest; never fetch or execute anything."""
    return json.loads(files(__package__).joinpath("manifest.json").read_text(encoding="utf-8"))


def load_profiles() -> tuple[Profile, ...]:
    """Expose experimental budgets; missing platform evidence remains unknown."""
    manifest = load_manifest()
    return tuple(
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
