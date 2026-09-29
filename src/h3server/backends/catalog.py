"""Immutable selected-weight contracts; no imports of inference code or network IO.

Pins and source rationale: docs/research/h3-pruned-runtime.md. Tokenizers belong
in the pinned runtime source tree, not in downloaded weight snapshots. A catalog
entry is source compatibility evidence, never a readiness or inference assertion.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re

from h3server.profiles import DiskEstimate, Profile, RecommendationStatus

COMFY_REVISION = "a7169322485d0049380fb207fa17e9fb3ec40486"
GGUF_REVISION = "6ea2651e7df66d7585f6ffee804b20e92fb38b8a"
GGUF_REPO = "Abiray/MiniMax-H3-Pruned-GGUF"
COLLECTION_REPO = "Abiray/Minimax-H3-nvfp4-INT4-INT8-Convrot"
ENCODER_REPO = "Winnougan/MiniMax-H3-INT4_Convrot_ComfyUI"
GGUF_MODEL_REVISION = "76061e7fe4866dbc68903bfdf852430f0f1b2487"
COLLECTION_REVISION = "908eccad7e68751190d04c171956f163bfeed741"
ENCODER_REVISION = "6387f8cd370fd8b4deaa9aa7e9e1be4d7298e7df"
ROLE_FOLDERS = {"diffusion_model": "diffusion_models", "text_encoder": "text_encoders",
                "video_vae": "vae", "audio_vae": "vae", "tokenizer": "comfy/text_encoders/qwen25_tokenizer"}


@dataclass(frozen=True)
class Artifact:
    role: str
    repository: str
    revision: str
    path: str
    size_bytes: int
    sha256: str
    local_relative_path: str

    def __post_init__(self) -> None:
        if self.role not in ROLE_FOLDERS:
            raise ValueError("unknown artifact role")
        for value in (self.path, self.local_relative_path):
            if (not isinstance(value, str) or not value or "\\" in value or
                any(p in {"", ".", ".."} for p in value.split("/")) or PurePosixPath(value).is_absolute()):
                raise ValueError("unsafe artifact path")
        if self.local_relative_path != ROLE_FOLDERS[self.role] + "/" + PurePosixPath(self.path).name:
            raise ValueError("artifact local folder must match role")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository):
            raise ValueError("invalid artifact repository")
        if not re.fullmatch(r"[0-9a-f]{40}", self.revision) or not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("invalid artifact pin")
        if type(self.size_bytes) is not int or self.size_bytes <= 0:
            raise ValueError("invalid artifact size")

    @property
    def url(self) -> str:
        if self.role == "tokenizer":
            return f"https://raw.githubusercontent.com/{self.repository}/{self.revision}/{self.path}"
        return f"https://huggingface.co/{self.repository}/resolve/{self.revision}/{self.path}"


@dataclass(frozen=True)
class RuntimeSource:
    repository: str
    revision: str


@dataclass(frozen=True)
class ArtifactSet:
    profile_name: str
    label: str
    runtime_id: str
    artifacts: tuple[Artifact, ...]
    tokenizers: tuple[Artifact, ...]
    runtime_sources: tuple[RuntimeSource, ...]
    publisher_vram_guidance: str
    required_gpu_family: str | None
    license_disclosures: tuple[str, ...]
    memory_status: str = "unmeasured"
    validation_status: str = "source-inspected-not-inference-certified"

    @property
    def digest(self) -> str:
        """Approval binding includes roles, local paths, auxiliaries and runtime pins."""
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @property
    def weight_bytes(self) -> int:
        return sum(a.size_bytes for a in self.artifacts)

    @property
    def tokenizer_bytes(self) -> int:
        return sum(a.size_bytes for a in self.tokenizers)

    @property
    def selected_bytes(self) -> int:
        return self.weight_bytes + self.tokenizer_bytes

    @property
    def runtime_bytes(self) -> int | None:
        """Source/dependency install size is unresolved, excluding known tokenizers."""
        return None

    @property
    def peak_weight_disk_bytes(self) -> int:
        """Fresh selected set only: partials renamed per file, no duplicate cache.

        Other snapshots, preexisting stale partials, runtime and outputs are extra.
        Cross-filesystem promotion fails rather than making an unbudgeted copy.
        """
        return self.weight_bytes


def _artifact(role, repo, revision, path, size, sha256):
    return Artifact(role, repo, revision, path, size, sha256, ROLE_FOLDERS[role] + "/" + PurePosixPath(path).name)


_AUXILIARIES = (
    _artifact("text_encoder", ENCODER_REPO, ENCODER_REVISION, "qwen3vl_32b_minimax_h3-int4_convrot.safetensors", 14173709116,
              "a97a557136057a8bcf6f2459b0875aeee3e8274408bd6616b669b41caefdb48d"),
    _artifact("video_vae", COLLECTION_REPO, COLLECTION_REVISION, "vae/minimax_h3_video_vae_fp16.safetensors", 5207808557,
              "88539d729fd7e9f9de765498428cd8e9152b8420317ec0e7542a362fffb7e0e1"),
    _artifact("audio_vae", COLLECTION_REPO, COLLECTION_REVISION, "vae/minimax_h3_audio_vae_fp32.safetensors", 605254869,
              "83043bf3afccf4cab4d7e114f6e04607e9687bef16930a2f6088d721a893d3c4"),
)
_TOKENIZERS = tuple(_artifact("tokenizer", "Comfy-Org/ComfyUI", COMFY_REVISION,
    "comfy/text_encoders/qwen25_tokenizer/" + name, size, digest) for name, size, digest in (
        ("merges.txt", 1671853, "8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5"),
        ("tokenizer_config.json", 8018, "49292bd4a58a43382bf01311bc3ed7a151a7f16fe07d155e900ea1af281555db"),
        ("vocab.json", 2776833, "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"),
    ))
_LICENSES = (
    "Abiray diffusion and VAE derivatives: MiniMax H3 Community License (publisher HF license: other); upstream MiniMaxAI/MiniMax-H3 terms apply. GGUF repository includes LICENSE and NOTICE.",
    "Winnougan INT4 ConvRot encoder: publisher metadata apache-2.0; derived MiniMax/Qwen encoder. Publisher metadata does not remove upstream derivative obligations.",
    "Runtime: preserve ComfyUI upstream licensing and ComfyUI-GGUF Apache-2.0 notices. Source investigation is not license acceptance or model-download approval.",
)
_VARIANTS = (
    ("h3-gguf-q4-k-m", "GGUF Q4_K_M", GGUF_REPO, GGUF_MODEL_REVISION, "MiniMax-H3-FL2VA-Pruned-Q4_K_M.gguf", 11564180576,
     "d74e644906fe95b8c7bf4837ba9d3f9392925f7301675a0993d419b62a4824a3", "Publisher recommends 16 GB VRAM; not a measured peak."),
    ("h3-gguf-q5-k-m", "GGUF Q5_K_M", GGUF_REPO, GGUF_MODEL_REVISION, "MiniMax-H3-FL2VA-Pruned-Q5_K_M.gguf", 14068966496,
     "abe739ac98bc14d6e4e56987198765b02899da632ad254c820f559b6dc4d1171", "Publisher recommends 24 GB VRAM; not a measured peak."),
    ("h3-convrot-mixed", "ConvRot mixed INT4/INT8", COLLECTION_REPO, COLLECTION_REVISION, "MiniMax_H3_FL2VA_pruned_mixed_int4_int8_convrot.safetensors", 15903012791,
     "541c7797d815a165a3b71fa6cfe03cafa1ba658cb03af1dc9f17cfe3985e7f99", "Publisher: 15+ GB VRAM (approximately 15.5 GB); not a measured peak."),
    ("h3-convrot-int8", "ConvRot INT8", COLLECTION_REPO, COLLECTION_REVISION, "MiniMax_H3_FL2VA_pruned_int8_convrot.safetensors", 20970379688,
     "f07a54277ca85b59e5243a924fb5c54d79768f820fc4155c1b614b6f613e9dbf", "Publisher: 24+ GB VRAM; not a measured peak."),
    ("h3-nvfp4", "NVFP4 diffusion + INT4 encoder", COLLECTION_REPO, COLLECTION_REVISION, "MiniMax_H3_FL2VA_pruned_nvfp4.safetensors", 12528636865,
     "6ab7f0c48141e7919b32f925ca3def22e06a6aebeb9e0b6f5a0be0fe8409976f", "Publisher: Blackwell only; VRAM/host-RAM peaks not measured."),
)
PRUNED_PROFILE_NAMES = tuple(v[0] for v in _VARIANTS)
UNAVAILABLE_VARIANTS = (("h3-convrot-int4", "Pure INT4 diffusion is absent from the pinned Abiray collection inventory; no URL is synthesized."),)


def get_artifact_set(profile_name: str) -> ArtifactSet:
    for name, reason in UNAVAILABLE_VARIANTS:
        if profile_name == name:
            raise ValueError("unavailable profile: " + reason)
    for name, label, repo, revision, path, size, digest, guidance in _VARIANTS:
        if name == profile_name:
            gguf = repo == GGUF_REPO
            sources = (RuntimeSource("Comfy-Org/ComfyUI", COMFY_REVISION),)
            if gguf:
                sources += (RuntimeSource("city96/ComfyUI-GGUF", GGUF_REVISION),)
            return ArtifactSet(name, label, "comfyui-h3-gguf" if gguf else "comfyui-h3-native",
                (_artifact("diffusion_model", repo, revision, path, size, digest), *_AUXILIARIES),
                _TOKENIZERS, sources, guidance, "blackwell" if name == "h3-nvfp4" else None, _LICENSES)
    raise ValueError("unknown pruned profile")


def pruned_profiles() -> tuple[Profile, ...]:
    result = []
    for name in PRUNED_PROFILE_NAMES:
        selected = get_artifact_set(name)
        result.append(Profile(name=name, status=RecommendationStatus.EXPERIMENTAL,
            evidence=("Source-inspected pinned ComfyUI H3; no real inference validation.", selected.publisher_vram_guidance,
                      "Host RAM and peak VRAM unknown; experimental, not a guaranteed fit."),
            min_gpu_count=1, requires_cuda_runtime=True, backend_revision=COMFY_REVISION,
            runtime_id=selected.runtime_id, artifact_set_digest=selected.digest,
            required_gpu_family=selected.required_gpu_family,
            disk=DiskEstimate(selected.selected_bytes, (
                "Selected weights plus code-bundled tokenizer only; per-file rename means no second weight copy.",
                "Runtime dependencies/source, existing snapshots/stale partials and outputs require additional disk; total provisioning size unknown.",
            ))))
    return tuple(result)
