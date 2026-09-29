"""Offline fixed H3 ComfyUI composition, never a Comfy server/workflow executor.

Only --request or bounded --diagnose child processes import native dependencies.
Runtime readiness is imports/CUDA/codecs + approved file integrity, NOT inference.
The parent retains the hard process-group deadline for uninterruptible kernels.
"""
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from dataclasses import asdict
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import sys
import threading
from types import SimpleNamespace

from h3server.backends.base import directory, read_json, stage
from h3server.backends.catalog import get_artifact_set, PRUNED_PROFILE_NAMES
from h3server.backends.comfy_runtime import verify_provisioned
from h3server.domain import (Capabilities, Capability, CapabilityStatus, Event,
                             GenerationSettings, JobRequest, Progress, StageStatus, GPUInfo, HardwareInfo)
from h3server.downloads import require_consent, verify_snapshot
from h3server.paths import AppPaths
from h3server.profiles import gpu_family_eligible

CAPABILITIES = Capabilities(
    lora=Capability(CapabilityStatus.UNSUPPORTED, ('Arbitrary LoRAs unsupported; the pinned optional Turbo preset has a separate validated path',)),
    sparse_attention=Capability(CapabilityStatus.SUPPORTED, ('ConvRot INT8 named sparse_preset only; requires pinned optional component; Turbo combination unsupported',)))


def parse_request(value):
    if type(value) is not dict or set(value) != {'prompt','loras','sparse_attention','settings'}:
        raise ValueError('unknown or missing generation input')
    if not isinstance(value['prompt'], str) or not 1 <= len(value['prompt']) <= 16000:
        raise ValueError('prompt size limit')
    if type(value['settings']) is not dict or set(value['settings']) != set(asdict(GenerationSettings())):
        raise ValueError('unknown or missing settings')
    settings = GenerationSettings(**value['settings'])
    get_artifact_set(settings.profile)
    if not isinstance(value['loras'], (tuple, list)):
        raise ValueError('invalid LoRA input')
    request = JobRequest(value['prompt'], tuple(value['loras']), value['sparse_attention'], settings)
    if request.sparse_attention is not None:
        raise ValueError('Use a named sparse_preset, not the legacy sparse_attention boolean')
    request.validate_capabilities(CAPABILITIES)
    return request


def load_runtime(environment, paths, snapshot, selected, *, cpu_smoke=False, prepare_weights=False, sage_attention=False):
    """Explicit imports from verified pinned sources; no plugin discovery/main.py.

    cpu_smoke is a Python-only test seam, not a daemon/generation CLI mode. It
    verifies native imports without asserting CUDA or executing model inference.
    """
    from h3server.backends.h3 import offline_environment
    offline_environment(paths)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(environment)/'ComfyUI'))
    import comfy.options
    comfy.options.enable_args_parsing(False)
    from comfy.cli_args import args, LatentPreviewMethod
    args.cpu = cpu_smoke
    args.use_sage_attention = sage_attention
    if sage_attention:
        from h3server.backends.sage_attention import require
        require(paths)
    args.base_directory = str(paths.tmp/'comfy')
    args.preview_method = LatentPreviewMethod.NoPreviews
    args.disable_all_custom_nodes = True
    args.disable_api_nodes = True
    args.enable_manager = False
    # main.py normally initializes/replaces the patcher for Aimdo DynamicVRAM.
    # We deliberately use upstream's ordinary ModelPatcher/offload path, also
    # used by GGUF, rather than assume that server startup happened.
    args.disable_dynamic_vram = True
    # folder_paths creates input_directory on import. Validate its parent before
    # letting that upstream side effect run, including on an interrupted restart.
    with directory(paths, 'tmp/comfy/input', create=True):
        pass
    # Do not activate optional downloaded/compiled kernels through user options.
    import folder_paths
    folder_paths.folder_names_and_paths.clear()
    from h3server.backends.safetensors_compat import artifact_directory, REPAIRS
    folders = {name: [str(snapshot/name)] for name in ('diffusion_models','text_encoders','vae')}
    for artifact in selected.artifacts:
        if prepare_weights and artifact.sha256 in REPAIRS:
            print(f'Preparing checksum-verified runtime copy: {Path(artifact.local_relative_path).name}', flush=True)
            normalized = artifact_directory(paths, snapshot, artifact)
            folders[Path(artifact.local_relative_path).parent.as_posix()].insert(0, str(normalized))
    for name, locations in folders.items():
        folder_paths.folder_names_and_paths[name] = (locations, {'.safetensors','.gguf'})
    with directory(paths, 'tmp/comfy/embeddings', create=True) as fd:
        if os.listdir(fd):
            raise ValueError('embeddings directory must remain empty')
    folder_paths.folder_names_and_paths['embeddings'] = ([str(paths.tmp/'comfy/embeddings')], set())
    folder_paths.filename_list_cache.clear()
    import torch
    import nodes
    import comfy.utils
    import comfy.model_management
    if sage_attention:
        import logging
        from comfy.ldm.modules import attention
        sage_kernel = attention.sageattn
        announced = [False]
        def traced_sage(*args, **kwargs):
            result = sage_kernel(*args, **kwargs)
            if not announced[0]:
                logging.info('SageAttention 1.0.6 kernel executed successfully for this generation')
                announced[0] = True
            return result
        attention.sageattn = traced_sage
    from comfy_extras.nodes_minimax_h3 import MiniMaxH3ImageToVideo, MiniMaxH3SigmaShift
    from comfy_extras.nodes_lt import LTXVSeparateAVLatent
    from comfy_extras.nodes_audio import VAEDecodeAudio
    from comfy_extras.nodes_video import CreateVideo
    gguf = None
    if selected.runtime_id == 'comfyui-h3-gguf':
        package = Path(environment)/'ComfyUI-GGUF'
        spec = importlib.util.spec_from_file_location('h3_pinned_gguf', package/'__init__.py', submodule_search_locations=[str(package)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        gguf = sys.modules['h3_pinned_gguf.nodes']
    return SimpleNamespace(torch=torch, nodes=nodes, gguf=gguf, utils=comfy.utils,
        mm=comfy.model_management, condition=MiniMaxH3ImageToVideo, shift=MiniMaxH3SigmaShift,
        separate=LTXVSeparateAVLatent, decode_audio=VAEDecodeAudio, create_video=CreateVideo)


def check_gpu(rt, selected):
    if not rt.torch.cuda.is_available():
        raise ValueError('CUDA GPU required')
    if selected.required_gpu_family:
        hardware = HardwareInfo(gpus=tuple(GPUInfo(rt.torch.cuda.get_device_name(i))
            for i in range(rt.torch.cuda.device_count())), gpu_inventory_complete=True)
        if gpu_family_eligible(hardware, selected.required_gpu_family) is not True:
            raise ValueError('positive Blackwell identification required for NVFP4')


@contextmanager
def execution_hooks(rt, emit, cancelled):
    previous = rt.utils.PROGRESS_BAR_HOOK
    current = ['loading']
    stop = threading.Event()
    def watch():
        while not stop.wait(.05):
            if cancelled():
                rt.mm.interrupt_current_processing(True)
                return
    def hook(value, total, preview, node_id=None):
        emit(Event(current[0], StageStatus.RUNNING, progress=Progress(value,
            'steps' if current[0] == 'denoising' else 'items', total)))
    rt.mm.interrupt_current_processing(False)
    rt.utils.set_progress_bar_global_hook(hook)
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    @contextmanager
    def run_stage(name):
        current[0] = name
        if cancelled(): rt.mm.interrupt_current_processing(True)
        rt.mm.throw_exception_if_processing_interrupted()
        with stage(name, emit):
            yield
            if cancelled(): rt.mm.interrupt_current_processing(True)
            rt.mm.throw_exception_if_processing_interrupted()
    try:
        yield run_stage
    finally:
        stop.set(); watcher.join()
        rt.utils.set_progress_bar_global_hook(previous)
        rt.mm.interrupt_current_processing(False)


def generate(request, output_path, rt, *, emit, cancelled=lambda: False):
    request = parse_request(asdict(request))
    settings = request.settings
    selected = get_artifact_set(settings.profile)
    files = {a.role: Path(a.local_relative_path).name for a in selected.artifacts}
    with execution_hooks(rt, emit, cancelled) as run_stage, rt.torch.inference_mode():
        with run_stage('loading'):
            if selected.runtime_id == 'comfyui-h3-gguf':
                model = rt.gguf.UnetLoaderGGUF().load_unet(files['diffusion_model'])[0]
            else:
                model = rt.nodes.UNETLoader().load_unet(files['diffusion_model'], 'default')[0]
            if settings.turbo_lora:
                from h3server.backends.turbo import apply
                model = apply(model, settings, rt.turbo)
                emit(Event('loading', StageStatus.RUNNING, 'Turbo v4-600 EMA LoRA applied; custom dual-schedule-aware sampler enabled.'))
            if settings.sparse_preset != 'off':
                from h3server.backends.sparse import apply, PRESETS
                model = apply(model, settings, rt.sparse)
                emit(Event('loading', StageStatus.RUNNING, f'Sparse {settings.sparse_preset}: {PRESETS[settings.sparse_preset]:.0%} target video budget, denser early ramp; dense attention retains the selected Sage/PyTorch path.'))
            clip = rt.nodes.CLIPLoader().load_clip(files['text_encoder'], 'minimax', 'default')[0]
            video_vae = rt.nodes.VAELoader().load_vae(files['video_vae'])[0]
            audio_vae = rt.nodes.VAELoader().load_vae(files['audio_vae'])[0]
        with run_stage('text_encoding'):
            positive, latent = rt.condition.execute(clip=clip, vae=video_vae, prompt=request.prompt,
                width=settings.width, height=settings.height, length=settings.num_frames,
                first_frame=None, last_frame=None).result
        with run_stage('denoising'):
            if settings.sparse_preset != 'off':
                from h3server.backends.sparse import trace_execution
                sparse_counts = trace_execution()
            model = rt.shift.execute(model, 12.0, 3.0)[0]
            if settings.turbo_lora:
                from h3server.backends.turbo import sample
                sampled = sample(model, positive, latent, settings, rt.turbo)
            else:
                sampled = rt.nodes.KSampler().sample(model=model, seed=settings.seed, steps=settings.steps,
                    cfg=1.0, sampler_name='res_multistep', scheduler='simple', positive=positive,
                    negative=[], latent_image=latent, denoise=1.0)[0]
            if settings.sparse_preset != 'off':
                calls = sparse_counts['native_sparse_calls']
                message = (f'Sparse runtime: native Kitchen INT8 executed {calls} sparse kernel calls.' if calls else
                           'Sparse runtime: no native sparse execution confirmed; a fallback was used. No sparse acceleration is claimed.')
                emit(Event('denoising', StageStatus.RUNNING, message))
            video_latent, audio_latent = rt.separate.execute(sampled).result
        with run_stage('video_decoding'):
            images = rt.nodes.VAEDecode().decode(video_vae, video_latent)[0]
        with run_stage('audio_decoding'):
            audio = rt.decode_audio.execute(vae=audio_vae, samples=audio_latent)[0]
        with run_stage('mux'):
            video = rt.create_video.execute(images=images, fps=24.0, audio=audio,
                bit_depth=8, color_space='sRGB', codec='none')[0]
            video.save_to(str(output_path))
    return dict(width=settings.width, height=settings.height, num_frames=settings.num_frames,
                fps=24, duration=settings.duration)


def runtime_diagnostic(paths, profile):
    report = dict(environment_ready=False, model_ready=False, model_loaded=False,
                  generation_tested=False, issues=[], profile=profile,
                  runtime_id=get_artifact_set(profile).runtime_id,
                  artifact_set_digest=get_artifact_set(profile).digest,
                  memory_status='unmeasured')
    try:
        import shutil, sysconfig
        if not (shutil.which('cc') or shutil.which('gcc')):
            raise RuntimeError('C compiler missing; install build-essential for Triton')
        if not (Path(sysconfig.get_path('include')) / 'Python.h').is_file():
            raise RuntimeError('Python development headers missing; install python3-dev for Triton')
        environment = verify_provisioned(paths, profile)
        from h3server.downloads import snapshot_path
        rt = load_runtime(environment, paths, snapshot_path(paths, profile_name=profile), get_artifact_set(profile))
        check_gpu(rt, get_artifact_set(profile))
        from triton.backends.nvidia.driver import CudaUtils
        CudaUtils()  # Compile/import the helper actually needed by text encoding.
        import av
        # Native import/allocation/kernel/codec smoke, never model loading.
        with rt.torch.inference_mode():
            x = rt.torch.ones((8,8), device='cuda', dtype=rt.torch.bfloat16)
            y = x @ x
            rt.torch.cuda.synchronize()
            if y.sum().item() != 512: raise ValueError('CUDA kernel failed')
        av.CodecContext.create('libx264','w'); av.CodecContext.create('aac','w')
        report['environment_ready'] = True
    except Exception as error:
        from h3server.installer.diagnostics import safe_log
        report['issues'].append(safe_log(f'{type(error).__name__}: {error}')[:2000])
    return report


def execute_document(request_path, *, emit, profile=None):
    from h3server.backends.h3 import offline_environment, validate_media
    path = Path(request_path).absolute()
    if path.name != 'request.json' or not re.fullmatch('[a-f0-9]{32}', path.parent.name) or path.parents[1].name != 'jobs' or path.parents[2].name != 'tmp':
        raise ValueError('invalid worker request path')
    paths = AppPaths(path.parents[3])
    document = read_json(paths, f'tmp/jobs/{path.parent.name}/request.json')
    if (type(document) is not dict or set(document) != {'schema_version','job_id','request','output_path','root'}
        or type(document['schema_version']) is not int or document['schema_version'] != 1
        or document['job_id'] != path.parent.name or document['root'] != str(paths.root)
        or document['output_path'] != str(path.parent/'video.mp4')):
        raise ValueError('invalid worker document')
    request = parse_request(document['request'])
    if profile is not None and request.settings.profile != profile:
        raise ValueError('request differs from active profile')
    profile = request.settings.profile
    require_consent(paths, profile_name=profile)
    offline_environment(paths)
    # Weights MUST fail offline before native imports or any node loading.
    emit(Event('loading', StageStatus.RUNNING, 'Verifying selected model file checksums before loading; large files can take time.'))
    snapshot = verify_snapshot(paths, profile_name=profile)
    emit(Event('loading', StageStatus.RUNNING, 'Model checksums verified; checking runtime and preparing model components.'))
    environment = verify_provisioned(paths, profile)
    if request.settings.sage_attention:
        emit(Event('loading', StageStatus.RUNNING, 'SageAttention 1.0.6 enabled for supported attention; masked/full-precision layers retain PyTorch attention.'))
    rt = load_runtime(environment, paths, snapshot, get_artifact_set(profile), prepare_weights=True,
                      sage_attention=request.settings.sage_attention)
    if request.settings.turbo_lora:
        from h3server.backends.turbo import load_node
        rt.turbo = load_node(paths, request.settings.turbo_artifact_digest)
    if request.settings.sparse_preset != 'off':
        from h3server.backends.sparse import require, load
        require(paths, request.settings.sparse_artifact_digest)
        rt.sparse = load(paths)
    check_gpu(rt, get_artifact_set(profile))
    cancelled = threading.Event()
    old_signal = signal.signal(signal.SIGTERM, lambda *args: cancelled.set())
    try:
        with directory(paths, f'tmp/jobs/{document["job_id"]}') as job_fd:
            if 'video.mp4' in os.listdir(job_fd): raise ValueError('output already exists')
            fd = os.open('partial.mp4', os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600, dir_fd=job_fd)
            os.close(fd)
            anchored = Path(f'/proc/self/fd/{job_fd}' if sys.platform.startswith('linux') else f'/dev/fd/{job_fd}')/'partial.mp4'
            try:
                generate(request, anchored, rt, emit=emit, cancelled=cancelled.is_set)
                with stage('validation', emit): validate_media(anchored, request.settings)
                require_consent(paths, profile_name=profile)
                if cancelled.is_set(): raise InterruptedError('cancelled')
                os.replace('partial.mp4','video.mp4', src_dir_fd=job_fd, dst_dir_fd=job_fd)
                os.fsync(job_fd)
            finally:
                try: os.unlink('partial.mp4', dir_fd=job_fd)
                except FileNotFoundError: pass
    finally:
        signal.signal(signal.SIGTERM, old_signal)


def backend(paths, profile):
    from h3server.backends.h3 import RuntimeReadiness, validate_media_fd
    from h3server.remote.worker import Backend
    selected = get_artifact_set(profile)
    command = (sys.executable, '-m', 'h3server.backends.pruned', '--profile', profile)
    readiness = RuntimeReadiness(paths, profile=profile, command=command)
    def validate(request):
        if parse_request(asdict(request)).settings.profile != profile:
            raise ValueError('request differs from active profile')
        if request.settings.sage_attention:
            from h3server.backends.sage_attention import require
            require(paths)
        if request.settings.turbo_lora:
            from h3server.backends.turbo import require
            require(paths, request.settings.turbo_artifact_digest)
        if request.settings.sparse_preset != 'off':
            from h3server.backends.sparse import require
            require(paths, request.settings.sparse_artifact_digest)
    capabilities = CAPABILITIES if profile == 'h3-convrot-int8' else Capabilities(
        lora=CAPABILITIES.lora, sparse_attention=Capability(CapabilityStatus.UNSUPPORTED, ('Sparse not verified for this profile',)))
    return Backend(command=command, readiness=readiness, capabilities=capabilities,
        validate_video=validate_media_fd, validate_request=validate, diagnostics=readiness.diagnostics,
        runtime_id=selected.runtime_id, profiles=(profile,))


def main(argv=None):
    import argparse
    from h3server.events import encode_event
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--request'); group.add_argument('--diagnose', action='store_true')
    parser.add_argument('--profile', required=True, choices=PRUNED_PROFILE_NAMES)
    parser.add_argument('--root'); parser.add_argument('--environment-only', action='store_true')
    args = parser.parse_args(argv)
    stdout = sys.stdout
    current_stage = ['loading']
    def emit(event):
        current_stage[0] = event.stage
        print(encode_event(event), file=stdout, flush=True)
    class WorkerLog:
        """Bounded line framing; scheduler redacts prompts and credentials."""
        def __init__(self):
            self.pending = ''
            self.discarding = False
        def write(self, text):
            for part in text.splitlines(keepends=True):
                if not self.discarding:
                    self.pending += part
                    if len(self.pending) > 4000:
                        self.pending = ''
                        self.discarding = True
                if part.endswith(('\n', '\r')):
                    if self.pending.strip():
                        emit(Event(current_stage[0], StageStatus.RUNNING, self.pending.rstrip()))
                    self.pending = ''
                    self.discarding = False
            return len(text)
        def flush(self):
            pass
        def isatty(self):
            return False
    sink = sys.stderr if args.diagnose else WorkerLog()
    with redirect_stdout(sink), redirect_stderr(sink):
        try:
            if not args.diagnose:
                import logging
                logging.basicConfig(level=logging.INFO, stream=sink, format='%(message)s')
            if args.diagnose:
                paths = AppPaths(args.root)
                report = runtime_diagnostic(paths, args.profile)
                if not args.environment_only:
                    try:
                        require_consent(paths, profile_name=args.profile)
                        verify_snapshot(paths, profile_name=args.profile)
                        report['model_ready'] = True
                    except (OSError,ValueError):
                        report['issues'].append('Selected model consent or integrity check failed')
                print(json.dumps(report), file=stdout, flush=True)
                if args.environment_only and not report['environment_ready']: raise SystemExit(1)
            else:
                if args.environment_only: raise ValueError('diagnostic flag in generation')
                execute_document(args.request, profile=args.profile, emit=emit)
        except BaseException as error:
            if not args.diagnose:
                from h3server.installer.diagnostics import safe_log
                emit(Event(current_stage[0], StageStatus.FAILED,
                           safe_log(f'{type(error).__name__}: {error}')[:4000]))
            raise SystemExit('Pruned H3 worker failed; no output published') from None


if __name__ == '__main__':
    main()
