"""Explicit application paths; construction and import never create directories."""

from dataclasses import dataclass
from pathlib import Path
import re


def _confined(path: Path, base: Path) -> Path:
    """Check existing symlinks, including ancestors of a not-yet-created path."""
    resolved = path.resolve()
    if not resolved.is_relative_to(base):
        raise ValueError(f"path escapes {base}: {path}")
    return resolved


def _portable_component(component: str) -> bool:
    # Device names are reserved even with extensions on Windows.
    stem = component.split(".", 1)[0].upper()
    return bool(
        re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", component)
        and not component.endswith(".")
        and stem not in {"CON", "PRN", "AUX", "NUL"}
        and not re.fullmatch(r"(?:COM|LPT)[1-9]", stem)
    )


@dataclass(frozen=True, init=False)
class AppPaths:
    """Paths on the machine running this code, not remote SSH path expansion.

    Checks reflect the current filesystem, not a race-free file-open guarantee.
    A future download handler must use a validated job record and guard against
    concurrent symlink replacement when opening files.
    """

    root: Path

    def __init__(self, root: str | Path = "~/MinimaxH3Server") -> None:
        object.__setattr__(self, "root", Path(root).expanduser().resolve())

    def _under_root(self, relative: str) -> Path:
        return _confined(self.root / relative, self.root)

    @property
    def releases(self) -> Path:
        return self._under_root("releases")

    @property
    def current(self) -> Path:
        # Keep the pointer's spelling so deployment can replace the symlink.
        _confined(self.root / "current", self.root)
        return self.root / "current"

    @property
    def environments(self) -> Path:
        return self._under_root("environments")

    @property
    def bootstrap(self) -> Path:
        return self._under_root("bootstrap")

    @property
    def bin(self) -> Path:
        return self._under_root("bin")

    @property
    def config(self) -> Path:
        return self._under_root("config")

    @property
    def state(self) -> Path:
        return self._under_root("state")

    @property
    def models(self) -> Path:
        return self._under_root("models")

    @property
    def loras(self) -> Path:
        return self._under_root("loras")

    @property
    def cache(self) -> Path:
        return self._under_root("cache")

    @property
    def cache_huggingface(self) -> Path:
        return self._under_root("cache/huggingface")

    @property
    def cache_torch(self) -> Path:
        return self._under_root("cache/torch")

    @property
    def cache_pip(self) -> Path:
        return self._under_root("cache/pip")

    @property
    def cache_cuda(self) -> Path:
        return self._under_root("cache/cuda")

    @property
    def cache_triton(self) -> Path:
        return self._under_root("cache/triton")

    @property
    def logs(self) -> Path:
        return self._under_root("logs")

    @property
    def tmp(self) -> Path:
        return self._under_root("tmp")

    @property
    def tmp_downloads(self) -> Path:
        return self._under_root("tmp/downloads")

    @property
    def tmp_jobs(self) -> Path:
        return self._under_root("tmp/jobs")

    def directories(self) -> tuple[Path, ...]:
        """Directories in parent-first order; current is a pointer, not a directory."""
        return (
            self.root, self.releases, self.environments, self.bootstrap, self.bin,
            self.config, self.state, self.models, self.loras, self.cache,
            self.cache_huggingface, self.cache_torch, self.cache_pip,
            self.cache_cuda, self.cache_triton, self.logs, self.tmp,
            self.tmp_downloads, self.tmp_jobs,
        )

    def cache_environment(self) -> dict[str, str]:
        """Overrides for child processes; never mutate os.environ or account HOME."""
        paths = {
            "XDG_CACHE_HOME": self.cache,
            "HF_HOME": self.cache_huggingface,
            "HF_HUB_CACHE": self._under_root("cache/huggingface/hub"),
            "HF_DATASETS_CACHE": self._under_root("cache/huggingface/datasets"),
            "HF_ASSETS_CACHE": self._under_root("cache/huggingface/assets"),
            "TORCH_HOME": self.cache_torch,
            "TORCH_EXTENSIONS_DIR": self._under_root("cache/torch/extensions"),
            "PIP_CACHE_DIR": self.cache_pip,
            "CUDA_CACHE_PATH": self.cache_cuda,
            "TRITON_CACHE_DIR": self.cache_triton,
            "TMPDIR": self.tmp,
            "TEMP": self.tmp,
            "TMP": self.tmp,
        }
        return {name: str(path) for name, path in paths.items()}

    def job_dir(self, job_id: str) -> Path:
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", job_id)
            or not _portable_component(job_id)
        ):
            raise ValueError("job_id must be a single opaque alphanumeric identifier")
        base = self.tmp_jobs
        expected = base / job_id
        resolved = _confined(expected, base)
        if resolved != expected:
            raise ValueError("job directory must not alias another path")
        return resolved

    def job_path(self, job_id: str, relative: str) -> Path:
        """Resolve an application-selected relative filename, not a download URL."""
        # Application-selected filenames use portable components on every host.
        if not relative or not all(_portable_component(part) for part in relative.split("/")):
            raise ValueError("job path must be a nonempty relative path without traversal")
        base = self.job_dir(job_id)
        return _confined(base / relative, base)
