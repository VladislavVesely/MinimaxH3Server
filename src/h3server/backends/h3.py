"""Pinned, offline MiniMax H3 modular t2va worker (experimental, not GPU certified).

Worker JSON schema is owned by remote.worker. No inference import occurs until
runtime_diagnostic/load_runtime inside a subprocess. --diagnose returns actual
installed-version/source-pin, CUDA kernel/BF16 and codec checks; it never claims
model loading or generation. With --root it also hashes the selected snapshot.

Setup contract: downloads.record_consent -> downloads.download_model, then poll
remote capabilities/health. Environment ready, verified files (model_ready), model
loaded and generation tested are distinct. No download is implicit in readiness
or generation. Native GPU/offload/LoRA acceptance remains an opt-in target test.

Managed LoRAs: loras/<id>.safetensors, one canonical Diffusers transformer LoRA,
metadata h3_partition=FL2VA and h3_model_revision=<manifest model pin>; optional
positive finite alpha. No arbitrary paths, pickle, DoRA, pruned/ref2va or int8 LoRA.
"""
from contextlib import contextmanager, redirect_stdout
from dataclasses import asdict
import hashlib
from importlib import metadata
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

from h3server.backends import load_manifest
from h3server.backends.base import directory, read_json, regular_file, stage
from h3server.domain import (Capabilities, Capability, CapabilityStatus, Event, GenerationSettings,
                             H3_PROFILES, JobRequest, Progress, StageStatus)
from h3server.downloads import require_consent, verify_snapshot
from h3server.paths import AppPaths

REQUIRED = ("transformer", "text_encoder", "tokenizer", "processor", "vae", "audio_vae", "scheduler", "audio_scheduler")
TRANSFORMER_EXCLUSIONS = ["proj_in", "audio_proj_in", "context_embedder", "time_embedder", "time_proj",
                          "token_refiner", "norm_out", "proj_out", "audio_proj_out"]
TEXT_EXCLUSIONS = ["model.visual", "model.language_model.embed_tokens", "model.language_model.norm", "lm_head"]
CAPABILITIES = Capabilities(
    lora=Capability(CapabilityStatus.SUPPORTED, ("Experimental BF16 only; one managed FL2VA canonical safetensors adapter; installed tensors verified",)),
    sparse_attention=Capability(CapabilityStatus.UNSUPPORTED, ("Pinned H3 implements dense attention only",)),
)


def parse_request(value):
    if type(value) is not dict or set(value) != {"prompt", "loras", "sparse_attention", "settings"}:
        raise ValueError("unknown or missing generation input")
    if not isinstance(value["prompt"], str) or not 1 <= len(value["prompt"]) <= 16000:
        raise ValueError("prompt size limit")
    loras = value["loras"]
    if not isinstance(loras, (list, tuple)) or len(loras) > 1 or any(
        not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item) for item in loras
    ):
        raise ValueError("only one managed LoRA identifier is supported")
    if type(value["settings"]) is not dict or set(value["settings"]) != set(asdict(GenerationSettings())):
        raise ValueError("unknown or missing settings")
    settings = GenerationSettings(**value["settings"])
    if settings.profile not in H3_PROFILES:
        raise ValueError("legacy adapter requires a legacy profile")
    request = JobRequest(value["prompt"], tuple(loras), value["sparse_attention"], settings)
    request.validate_capabilities(CAPABILITIES)
    if loras and settings.profile == H3_PROFILES[1]:
        raise ValueError("int8 plus LoRA is not supported")
    return request


def check_dependencies():
    """Check actual installed distributions and exact inspected Diffusers source.

    Candidate constraints are not a resolved lock. pip check also checks installed
    transitive dependencies; source fingerprints reject a wrong release/editable
    checkout even if its version string happens to match.
    """
    from packaging.specifiers import SpecifierSet
    manifest = load_manifest()
    issues = []
    for name, constraint in manifest["candidate_environment"]["constraints"].items():
        if name == "diffusers":
            continue
        try:
            if not SpecifierSet(constraint).contains(metadata.version(name)):
                issues.append(name + " version does not meet pinned candidate constraint")
        except metadata.PackageNotFoundError:
            issues.append(name + " is not installed")
    try:
        dist = metadata.distribution("diffusers")
        direct_url = json.loads(dist.read_text("direct_url.json") or "{}")
        if direct_url.get("vcs_info", {}).get("commit_id") != manifest["backend"]["revision"]:
            issues.append("Diffusers installation does not attest the pinned Git revision")
        for key in ("h3_pipeline", "h3_denoise", "h3_before_denoise", "h3_decoders", "h3_transformer", "lora_loader", "modular_pipeline", "export"):
            source = manifest["sources"][key]
            relative = source["url"].split("/src/", 1)[1]
            if hashlib.sha256(Path(dist.locate_file(relative)).read_bytes()).hexdigest() != source["sha256"]:
                issues.append("diffusers source mismatch: " + key)
    except (metadata.PackageNotFoundError, OSError):
        issues.append("pinned Diffusers sources are not installed")
    if not issues:
        result = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, timeout=60, check=False)
        if result.returncode:
            issues.append("installed dependency consistency check failed")
    return issues


def load_runtime():
    import torch
    from diffusers import ComponentsManager, ModularPipeline, MiniMaxH3Transformer3DModel, TorchAoConfig
    from diffusers.hooks import apply_group_offloading
    from diffusers.modular_pipelines.minimax_h3.denoise import MiniMaxH3DenoiseLoopWrapper
    from diffusers.modular_pipelines.minimax_h3.encoders import MiniMaxH3TextEncoderStep
    from diffusers.modular_pipelines.minimax_h3.decoders import MiniMaxH3VideoDecodeStep, MiniMaxH3AudioDecodeStep
    from diffusers.utils.export_utils import encode_video
    from transformers import Qwen3VLForConditionalGeneration, TorchAoConfig as TransformersTorchAoConfig
    from torchao.quantization import Int8WeightOnlyConfig
    return SimpleNamespace(torch=torch, ComponentsManager=ComponentsManager, ModularPipeline=ModularPipeline,
        Transformer=MiniMaxH3Transformer3DModel, TextEncoder=Qwen3VLForConditionalGeneration,
        DiffusersTorchAoConfig=TorchAoConfig, TransformersTorchAoConfig=TransformersTorchAoConfig,
        Int8WeightOnlyConfig=Int8WeightOnlyConfig, apply_group_offloading=apply_group_offloading,
        Denoise=MiniMaxH3DenoiseLoopWrapper, stage_classes={"text_encoding": MiniMaxH3TextEncoderStep,
        "video_decoding": MiniMaxH3VideoDecodeStep, "audio_decoding": MiniMaxH3AudioDecodeStep}, encode_video=encode_video)


def runtime_diagnostic():
    report = {"environment_ready": False, "model_ready": False, "model_loaded": False,
              "generation_tested": False, "issues": [], "versions": {},
              "backend_revision": load_manifest()["backend"]["revision"]}
    try:
        report["issues"] = check_dependencies()
        if report["issues"]:
            return report
        rt = load_runtime()
        import av
        if not rt.torch.cuda.is_available() or not rt.torch.cuda.is_bf16_supported():
            raise RuntimeError("CUDA/BF16 unavailable")
        with rt.torch.inference_mode():
            x = rt.torch.ones((8, 8), device="cuda", dtype=rt.torch.bfloat16)
            y = x @ x
            rt.torch.cuda.synchronize()
            if y.sum().item() != 512:
                raise RuntimeError("CUDA kernel failed")
        av.CodecContext.create("libx264", "w")
        av.CodecContext.create("aac", "w")
        report["versions"] = {name: metadata.version(name) for name in load_manifest()["candidate_environment"]["constraints"]}
        report["cuda"] = rt.torch.version.cuda
        report["gpu"] = rt.torch.cuda.get_device_name(0)
        report["environment_ready"] = True
    except Exception:
        report["issues"] = ["Runtime import, CUDA BF16 kernel, dependency or codec diagnostic failed"]
    return report


def load_pipeline(snapshot, profile, rt, *, lora=None):
    if profile not in H3_PROFILES:
        raise ValueError("unknown profile")
    if lora is not None and profile != H3_PROFILES[0]:
        raise ValueError("int8 plus LoRA is not supported")
    revision = load_manifest()["model"]["revision"]
    local = dict(revision=revision, local_files_only=True, trust_remote_code=False)
    manager = rt.ComponentsManager()
    pipe = rt.ModularPipeline.from_pretrained(str(snapshot), workflow="t2va", components_manager=manager, **local)
    # Pinned upstream strips trust_remote_code if the spec still names the Hub,
    # BEFORE applying load_components overrides. get_component_spec returns a COPY.
    # Rebind in-memory specs explicitly; never rewrite the verified on-disk index.
    for name in REQUIRED:
        spec = pipe._component_specs[name]
        spec.pretrained_model_name_or_path = str(snapshot)
        spec.revision = revision
        spec.subfolder = name
    names = list(REQUIRED)
    weights = dict(**local, dtype=rt.torch.bfloat16, use_safetensors=True)
    if profile == H3_PROFILES[1]:
        transformer = rt.Transformer.from_pretrained(str(snapshot), subfolder="transformer", **weights,
            low_cpu_mem_usage=False, quantization_config=rt.DiffusersTorchAoConfig(
                rt.Int8WeightOnlyConfig(version=2), modules_to_not_convert=TRANSFORMER_EXCLUSIONS))
        text_encoder = rt.TextEncoder.from_pretrained(str(snapshot), subfolder="text_encoder", **weights,
            quantization_config=rt.TransformersTorchAoConfig(
                rt.Int8WeightOnlyConfig(version=2), modules_to_not_convert=TEXT_EXCLUSIONS))
        pipe.update_components(transformer=transformer, text_encoder=text_encoder)
        names = [n for n in names if n not in ("transformer", "text_encoder")]
    pipe.load_components(names=names, pretrained_model_name_or_path={n: str(snapshot) for n in names},
                         subfolder={n:n for n in names}, **weights)
    missing = [name for name in REQUIRED if getattr(pipe, name, None) is None]
    if missing:
        raise RuntimeError("Required H3 components failed to load: " + ", ".join(missing))
    if lora is not None:
        install_lora(pipe, lora, rt)
    if profile == H3_PROFILES[0]:
        manager.enable_auto_cpu_offload(device="cuda", memory_reserve_margin="12GB")
    else:
        pipe.transformer.requires_grad_(False)
        pipe.text_encoder.requires_grad_(False)
        offload = dict(onload_device=rt.torch.device("cuda"), offload_device=rt.torch.device("cpu"), use_stream=True)
        pipe.transformer.enable_group_offload(offload_type="block_level", num_blocks_per_group=1, **offload)
        rt.apply_group_offloading(pipe.text_encoder.model, offload_type="leaf_level", **offload)
        pipe.vae.to("cuda")
        pipe.audio_vae.to("cuda")
    return pipe


@contextmanager
def instrument_progress(rt, emit):
    """Process-local pinned class hooks, always restored (also after exceptions).

    Patch the factory actually called by the denoise loop. Upstream supplies
    len(block_state.timesteps), NOT the request's sigma-grid point count.
    Python special-method lookup requires class-level __call__ hooks for stages.
    This context is only used in the isolated single-job process, not the daemon.
    """
    class Bar:
        def __init__(self, total):
            if type(total) is not int or total < 1:
                raise ValueError("denoise loop did not provide actual timesteps")
            self.total, self.completed = total, 0
        def report(self, status):
            emit(Event("denoising", status, progress=Progress(self.completed, "steps", self.total)))
        def __enter__(self):
            self.report(StageStatus.RUNNING)
            return self
        def update(self, n=1):
            if type(n) is not int or n != 1:
                raise ValueError("unexpected denoise progress update")
            self.completed += n
            self.report(StageStatus.RUNNING)
        def __exit__(self, typ, value, tb):
            status = StageStatus.FAILED if typ or self.completed != self.total else StageStatus.COMPLETED
            self.report(status)
            if typ is None and self.completed != self.total:
                raise RuntimeError("incomplete denoise loop")
    patches = []
    def patch(cls, name, method):
        patches.append((cls, name, cls.__dict__.get(name), name in cls.__dict__))
        setattr(cls, name, method)
    def factory(self, iterable=None, total=None):
        if iterable is not None:
            raise ValueError("unexpected denoise iterator")
        return Bar(total)
    try:
        patch(rt.Denoise, "progress_bar", factory)
        for stage_name, cls in rt.stage_classes.items():
            original = cls.__call__
            def call(self, *args, _original=original, _stage=stage_name, **kwargs):
                with stage(_stage, emit):
                    return _original(self, *args, **kwargs)
            patch(cls, "__call__", call)
        yield
    finally:
        for cls, name, original, owned in reversed(patches):
            if owned:
                setattr(cls, name, original)
            else:
                delattr(cls, name)


def read_lora(paths, identifier):
    """Load only bounded local safetensors, validate metadata before pipeline load."""
    from safetensors import safe_open
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
        raise ValueError("invalid managed LoRA identifier")
    relative = "loras/" + identifier + ".safetensors"
    with regular_file(paths, relative) as stream:
        info = os.fstat(stream.fileno())
        if not 1 <= info.st_size <= 2 * 1024**3:
            raise ValueError("LoRA size limit")
        # safetensors opens a filename. An already-open descriptor prevents a
        # path/symlink replacement between confinement and tensor parsing.
        fd_path = f"/proc/self/fd/{stream.fileno()}" if sys.platform.startswith("linux") else f"/dev/fd/{stream.fileno()}"
        with safe_open(fd_path, framework="pt", device="cpu") as handle:
            meta = handle.metadata() or {}
            if (meta.get("h3_partition") != "FL2VA" or meta.get("h3_model_revision") != load_manifest()["model"]["revision"]
                or set(meta) - {"h3_partition", "h3_model_revision", "alpha"}):
                raise ValueError("LoRA requires compatible FL2VA metadata")
            if "alpha" in meta:
                alpha = float(meta["alpha"])
                if not math.isfinite(alpha) or alpha <= 0:
                    raise ValueError("invalid LoRA alpha")
            else:
                alpha = None
            keys = list(handle.keys())
            if not keys or len(keys) > 20000 or any(not re.fullmatch(
                r"transformer\.[A-Za-z0-9_.]+\.lora_[AB]\.weight", key
            ) for key in keys):
                raise ValueError("unsupported LoRA keys; canonical Diffusers only, no DoRA")
            tensors = {key: handle.get_tensor(key) for key in keys}
    return tensors, alpha


def install_lora(pipe, lora, rt):
    tensors, alpha = lora
    pairs = {}
    for key, tensor in tensors.items():
        match = re.fullmatch(r"transformer\.(.+)\.lora_([AB])\.weight", key)
        if (not match or tensor.ndim != 2 or not tensor.is_floating_point()
            or not rt.torch.isfinite(tensor).all().item() or not rt.torch.count_nonzero(tensor).item()):
            raise ValueError("invalid or empty LoRA factors")
        pairs.setdefault(match[1], {})[match[2]] = tensor
    if not pairs:
        raise ValueError("empty LoRA")
    for name, factors in pairs.items():
        if set(factors) != {"A", "B"}:
            raise ValueError("unpaired LoRA factors")
        module = pipe.transformer.get_submodule(name)
        a, b = factors["A"], factors["B"]
        if a.shape[0] != b.shape[1] or (b.shape[0], a.shape[1]) != tuple(module.weight.shape):
            raise ValueError("LoRA shape mismatch or pruned checkpoint")
    # A validated tensor dictionary avoids reopening files and pickle fallback.
    # Fold optional alpha into B; loader's alpha-less contract is alpha==rank.
    state = {key: tensor.clone() for key, tensor in tensors.items()}
    if alpha is not None:
        for name, factors in pairs.items():
            state[f"transformer.{name}.lora_B.weight"] *= alpha / factors["A"].shape[0]
    adapter = "h3_managed"
    pipe.load_lora_weights(state, adapter_name=adapter, load_into_transformer_ref=False,
                           use_safetensors=True, local_files_only=True)
    if adapter not in getattr(pipe.transformer, "peft_config", {}):
        raise RuntimeError("LoRA loader did not install adapter")
    pipe.transformer.to(dtype=rt.torch.bfloat16)
    for name in pairs:
        module = pipe.transformer.get_submodule(name)
        if adapter not in module.active_adapters:
            raise RuntimeError("LoRA adapter is inactive")
        for side in ("A", "B"):
            installed = getattr(module, "lora_" + side)[adapter].weight
            expected = state[f"transformer.{name}.lora_{side}.weight"].to(dtype=installed.dtype, device=installed.device)
            if (not rt.torch.equal(installed, expected) or not rt.torch.isfinite(installed).all().item()
                or not rt.torch.count_nonzero(installed).item()):
                raise RuntimeError("LoRA factors were not installed or are unusable in BF16")


def _validate_container(stream, settings=None):
    import av
    with av.open(stream, mode="r") as container:
        videos, audios = list(container.streams.video), list(container.streams.audio)
        if "mp4" not in container.format.name.split(",") or len(videos) != 1 or len(audios) != 1:
            raise ValueError("expected MP4 with one video and audio stream")
        video, audio = videos[0], audios[0]
        if (video.average_rate != 24 or not 32 <= video.width <= 1344 or not 32 <= video.height <= 1344
            or video.width * video.height > 1344 * 768 or audio.rate != 32000 or len(audio.layout.channels) != 2):
            raise ValueError("unexpected H3 media geometry/rate")
        count, samples = 0, 0
        for packet in container.demux(video, audio):
            for frame in packet.decode():
                if packet.stream.type == "video":
                    count += 1
                    if settings and (frame.width, frame.height) != (settings.width, settings.height):
                        raise ValueError("video dimensions do not match request")
                else:
                    samples += frame.samples
                if count > 345 or samples > 16 * 32000:
                    raise ValueError("media duration limit")
        if not 124 <= count <= 345 or samples <= 0 or abs(samples / 32000 - count / 24) > .25:
            raise ValueError("truncated or unsynchronized media")
        if settings and count != settings.num_frames:
            raise ValueError("video frame count does not match request")


def validate_media(path, settings):
    # Parent job directory is descriptor-validated by execute_document.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        import stat
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= 4 * 1024**3:
            raise ValueError("invalid output file")
        _validate_container(stream, settings)


def validate_media_fd(fd):
    try:
        with os.fdopen(os.dup(fd), "rb") as stream:
            stream.seek(0)
            _validate_container(stream)
        return True
    except Exception:
        return False


def generate(pipe, request, output_path, rt, *, emit, validator=validate_media):
    request = parse_request(asdict(request))
    settings = request.settings
    with rt.torch.inference_mode():
        result = pipe(prompt=request.prompt, num_frames=settings.num_frames, height=settings.height,
                      width=settings.width, num_inference_steps=settings.steps,
                      generator=rt.torch.Generator().manual_seed(settings.seed),
                      output=["videos", "audio", "sampling_rate"])
    # Upstream encode_video combines export/mux, so do not invent separate counts.
    with stage("mux", emit):
        rt.encode_video(result["videos"][0], fps=24, output_path=str(output_path),
                        audio=result["audio"][0], audio_sample_rate=result["sampling_rate"])
    with stage("validation", emit):
        validator(output_path, settings)


def offline_environment(paths):
    os.environ.update(paths.cache_environment())
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    # tempfile may have cached a prior default before overrides were applied.
    import tempfile
    tempfile.tempdir = str(paths.tmp)


def execute_document(request_path, *, emit):
    path = Path(request_path).absolute()
    # Fixed worker location; do not use an unvalidated root from arbitrary JSON.
    if path.name != "request.json" or not re.fullmatch(r"[a-f0-9]{32}", path.parent.name) or path.parents[1].name != "jobs" or path.parents[2].name != "tmp":
        raise ValueError("invalid worker request path")
    paths = AppPaths(path.parents[3])
    relative = f"tmp/jobs/{path.parent.name}/request.json"
    document = read_json(paths, relative)
    if (type(document) is not dict or set(document) != {"schema_version", "job_id", "request", "output_path", "root"}
        or type(document["schema_version"]) is not int or document["schema_version"] != 1
        or document["job_id"] != path.parent.name or document["root"] != str(paths.root)
        or document["output_path"] != str(path.parent / "video.mp4")):
        raise ValueError("invalid worker document")
    request = parse_request(document["request"])
    require_consent(paths)
    offline_environment(paths)
    # Reserve exclusively, then give PyAV an anchored directory descriptor path.
    with directory(paths, f"tmp/jobs/{document['job_id']}") as job_fd:
        fd = os.open("video.mp4", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=job_fd)
        os.close(fd)
        anchored = Path(f"/proc/self/fd/{job_fd}" if sys.platform.startswith("linux") else f"/dev/fd/{job_fd}") / "video.mp4"
        try:
            with stage("loading", emit):
                snapshot = verify_snapshot(paths)
                diagnostic = runtime_diagnostic()
                if not diagnostic["environment_ready"]:
                    raise RuntimeError("runtime diagnostic failed")
                rt = load_runtime()
                lora = read_lora(paths, request.loras[0]) if request.loras else None
                pipe = load_pipeline(snapshot, request.settings.profile, rt, lora=lora)
            require_consent(paths)
            with instrument_progress(rt, emit):
                generate(pipe, request, anchored, rt, emit=emit)
        except BaseException:
            os.unlink("video.mp4", dir_fd=job_fd)
            raise


class RuntimeReadiness:
    """Bounded background diagnostic, never torch imports in the daemon/event loop.

    Initial checks block admission. Successful results remain usable during a
    background refresh; active generation defers expensive refreshes. Each
    execution independently rechecks consent, full hashes and runtime.
    No saved report is trusted as proof of the current installed environment.
    """
    def __init__(self, paths, *, profile=None, command=None):
        self.paths = paths
        self.profile = profile
        self.command = command or (sys.executable, "-m", "h3server.backends.h3")
        self.report = {"environment_ready": False, "model_ready": False, "model_loaded": False, "generation_tested": False,
                       "issues": ["Runtime/model diagnostic has not run"]}
        self.lock = threading.Lock()
        self.thread = None
        self.checked = 0

    def _check(self):
        try:
            env = {key: os.environ[key] for key in ("PATH", "LANG", "LD_LIBRARY_PATH", "CUDA_VISIBLE_DEVICES") if key in os.environ}
            env.update(self.paths.cache_environment())
            env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
            result = subprocess.run([*self.command, "--diagnose", "--root", str(self.paths.root)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, timeout=1800, check=False)
            if result.returncode or len(result.stdout) > 16384:
                raise ValueError("diagnostic failed")
            report = json.loads(result.stdout)
        except Exception:
            report = {"environment_ready": False, "model_ready": False, "model_loaded": False, "generation_tested": False,
                      "issues": ["Runtime/model diagnostic process failed"]}
        with self.lock:
            self.report = {**report, "checked_at": time.time()}
            self.checked = time.monotonic()

    def diagnostics(self):
        with self.lock:
            return json.loads(json.dumps(self.report))

    def __call__(self, *, refresh=True):
        try:
            require_consent(self.paths, profile_name=self.profile)
        except (OSError, ValueError):
            return ("Persisted model download/license/eligibility consent is required",)
        with self.lock:
            if refresh and time.monotonic() - self.checked > 60 and (self.thread is None or not self.thread.is_alive()):
                self.thread = threading.Thread(target=self._check, daemon=True)
                self.thread.start()
            if not self.report.get("environment_ready") or not self.report.get("model_ready"):
                if self.thread is not None and self.thread.is_alive():
                    return ("Runtime and verified model diagnostic is running",)
                return tuple(self.report["issues"]) or ("Runtime or model is not ready",)
            return ()


def backend(paths):
    from h3server.remote.worker import Backend
    def validate_request(request):
        normalized = parse_request(asdict(request))
        for identifier in normalized.loras:
            try:
                with regular_file(paths, "loras/" + identifier + ".safetensors") as stream:
                    if not 1 <= os.fstat(stream.fileno()).st_size <= 2 * 1024**3:
                        raise ValueError("LoRA size limit")
            except OSError as exc:
                raise ValueError("managed LoRA is not available") from exc
    readiness = RuntimeReadiness(paths)
    return Backend(readiness=readiness, capabilities=CAPABILITIES, validate_video=validate_media_fd,
                   validate_request=validate_request, diagnostics=readiness.diagnostics)


def main(argv=None):
    import argparse
    from h3server.events import encode_event
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--request")
    group.add_argument("--diagnose", action="store_true")
    parser.add_argument("--root")
    args = parser.parse_args(argv)
    stdout = sys.stdout
    emit = lambda event: print(encode_event(event), file=stdout, flush=True)
    # Upstream prints/logs must never corrupt the JSONL protocol.
    with redirect_stdout(sys.stderr):
        try:
            if args.diagnose:
                paths = AppPaths(args.root) if args.root else None
                if paths:
                    offline_environment(paths)
                report = runtime_diagnostic()
                if paths:
                    try:
                        require_consent(paths)
                        verify_snapshot(paths)
                        report["model_ready"] = True
                    except (OSError, ValueError):
                        report["issues"].append("Model consent or complete snapshot integrity check failed")
                print(json.dumps(report), file=stdout, flush=True)
            else:
                execute_document(args.request, emit=emit)
        except Exception:
            raise SystemExit("H3 worker failed; no output published") from None


if __name__ == "__main__":
    main()
