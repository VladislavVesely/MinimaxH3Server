"""Controlled production-node IO, NOT numerical inference or downloaded weights."""
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace as NS
import json
import os
import pytest

from h3server.backends import pruned
from h3server.backends.catalog import get_artifact_set, PRUNED_PROFILE_NAMES
from h3server.domain import GenerationSettings, JobRequest
from h3server.paths import AppPaths


class NodeOutput:
    # V3 isn't a legacy tuple; production code must honor .result / indexing.
    def __init__(self, *values): self.result = values
    def __getitem__(self, index): return self.result[index]


@pytest.fixture
def runtime():
    calls = []
    def legacy(name, result):
        def call(*args, **kw):
            calls.append((name, args, kw))
            return (result,)
        return call
    def v3(name, *result):
        def call(*args, **kw):
            calls.append((name, args, kw))
            return NodeOutput(*result)
        return NS(execute=call)
    def save(path):
        calls.append(('save', (path,), {}))
        output = Path(path)
        if str(output).startswith(('/dev/fd/', '/proc/self/fd/')):
            # macOS cannot reopen a directory through /dev/fd/N/name. Emulate
            # only the native export boundary with the real validated dirfd;
            # Linux production PyAV uses /proc/self/fd/N/name directly.
            fd = os.open(output.name, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW,
                         dir_fd=int(output.parent.name))
            with os.fdopen(fd, 'wb') as stream:
                stream.write(b'controlled fixture, NOT a real MP4')
        else:
            output.write_bytes(b'controlled fixture, NOT a real MP4')
    utils = NS(PROGRESS_BAR_HOOK='old-hook')
    utils.set_progress_bar_global_hook = lambda value: setattr(utils, 'PROGRESS_BAR_HOOK', value)
    class Interrupt(BaseException): pass
    mm = NS(interrupted=False)
    def interrupt(value): mm.interrupted = value
    def check():
        if mm.interrupted: raise Interrupt()
    mm.interrupt_current_processing = interrupt
    mm.throw_exception_if_processing_interrupted = check
    rt = NS(torch=NS(inference_mode=nullcontext), utils=utils, mm=mm,
        nodes=NS(UNETLoader=lambda: NS(load_unet=legacy('native', 'model')),
            CLIPLoader=lambda: NS(load_clip=legacy('clip', 'clip')),
            VAELoader=lambda: NS(load_vae=legacy('vae', 'vae')),
            KSampler=lambda: NS(sample=legacy('sample', 'sampled')),
            VAEDecode=lambda: NS(decode=legacy('decode_video', 'images'))),
        gguf=NS(UnetLoaderGGUF=lambda: NS(load_unet=legacy('gguf', 'model'))),
        condition=v3('condition', 'positive', 'latent'), shift=v3('shift', 'shifted'),
        separate=v3('separate', 'video_latent', 'audio_latent'),
        decode_audio=v3('decode_audio', 'audio'), create_video=v3('create_video', NS(save_to=save)))
    return rt, calls


def request(profile=PRUNED_PROFILE_NAMES[0]):
    return JobRequest('raw prompt', settings=GenerationSettings(profile=profile, steps=20, seed=123))


@pytest.mark.parametrize('profile', PRUNED_PROFILE_NAMES)
def test_fixed_production_composition(runtime, tmp_path, profile):
    rt, calls = runtime
    events = []
    result = pruned.generate(request(profile), tmp_path / 'partial.mp4', rt, emit=events.append)
    selected = get_artifact_set(profile)
    expected = 'gguf' if selected.runtime_id.endswith('gguf') else 'native'
    assert calls[0][0] == expected
    assert calls[0][1][0] == Path(selected.artifacts[0].path).name
    assert [c[0] for c in calls] == [expected, 'clip', 'vae', 'vae', 'condition', 'shift', 'sample', 'separate', 'decode_video', 'decode_audio', 'create_video', 'save']
    assert calls[1][1] == (Path(selected.artifacts[1].path).name, 'minimax', 'default')
    assert calls[4][2] == dict(clip='clip', vae='vae', prompt='raw prompt', width=1344, height=768, length=124, first_frame=None, last_frame=None)
    assert calls[5][1] == ('model', 12.0, 3.0)
    assert calls[6][2] == dict(model='shifted', seed=123, steps=20, cfg=1.0, sampler_name='res_multistep', scheduler='simple', positive='positive', negative=[], latent_image='latent', denoise=1.0)
    assert calls[7][1] == ('sampled',)
    assert calls[9][2] == dict(vae='vae', samples='audio_latent')
    assert calls[10][2] == dict(images='images', fps=24.0, audio='audio', bit_depth=8, color_space='sRGB', codec='none')
    assert result == dict(width=1344, height=768, num_frames=124, fps=24, duration=124/24)
    assert rt.utils.PROGRESS_BAR_HOOK == 'old-hook' and not rt.mm.interrupted
    assert {e.stage for e in events} == {'loading', 'text_encoding', 'denoising', 'video_decoding', 'audio_decoding', 'mux'}


@pytest.mark.parametrize('field,value', [('workflow', {}), ('negative_prompt','no'), ('cfg',2), ('fps',30), ('images',[])])
def test_reject_unmapped_inputs(field, value):
    data = asdict(request()); data[field] = value
    with pytest.raises(ValueError): pruned.parse_request(data)


def test_cancellation_baseexception_restores_hook(runtime, tmp_path):
    rt, calls = runtime
    def stop(*args, **kw):
        rt.mm.interrupt_current_processing(True)
        rt.mm.throw_exception_if_processing_interrupted()
    rt.condition.execute = stop
    with pytest.raises(BaseException):
        pruned.generate(request(), tmp_path / 'partial.mp4', rt, emit=lambda e: None)
    assert rt.utils.PROGRESS_BAR_HOOK == 'old-hook' and not rt.mm.interrupted
    assert not any(c[0] == 'save' for c in calls)


def test_stage_local_progress(runtime, tmp_path):
    rt, _ = runtime; events = []
    def sample(**kw):
        rt.utils.PROGRESS_BAR_HOOK(2, 20, None, node_id='ignored')
        return ('sampled',)
    rt.nodes.KSampler = lambda: NS(sample=sample)
    pruned.generate(request(), tmp_path/'partial.mp4', rt, emit=events.append)
    p = [e for e in events if e.progress]
    assert len(p) == 1 and p[0].stage == 'denoising'
    assert (p[0].progress.completed, p[0].progress.total, p[0].progress.unit) == (2, 20, 'steps')


def test_missing_weights_fail_before_runtime_import(tmp_path, monkeypatch):
    paths = AppPaths(tmp_path/'app')
    for d in paths.directories(): d.mkdir(parents=True, exist_ok=True)
    job = paths.tmp/'jobs'/('a'*32); job.mkdir()
    document = dict(schema_version=1, job_id=job.name, request=asdict(request()), root=str(paths.root), output_path=str(job/'video.mp4'))
    (job/'request.json').write_text(json.dumps(document))
    monkeypatch.setattr(pruned, 'require_consent', lambda *a, **kw: None)
    monkeypatch.setattr(pruned, 'load_runtime', lambda *a, **kw: pytest.fail('must not import runtime'))
    with pytest.raises((OSError,ValueError)):
        pruned.execute_document(job/'request.json', emit=lambda e: None)
    assert not (job/'video.mp4').exists()


def test_dispatch_never_diffusers(tmp_path):
    from h3server.backends import select_backend
    b = select_backend(AppPaths(tmp_path), profile=PRUNED_PROFILE_NAMES[0])
    assert b.command[2] == 'h3server.backends.pruned'
    assert b.profiles == (PRUNED_PROFILE_NAMES[0],)
    with pytest.raises(ValueError): b.validate_request(request(PRUNED_PROFILE_NAMES[1]))
    with pytest.raises(ValueError): select_backend(AppPaths(tmp_path), profile='not-a-profile')


def test_nvfp4_positive_blackwell_only():
    for name in ['NVIDIA GeForce RTX 4090', 'Unknown GPU']:
        rt = NS(torch=NS(cuda=NS(is_available=lambda: True, device_count=lambda: 1, get_device_name=lambda i: name)))
        with pytest.raises(ValueError): pruned.check_gpu(rt, get_artifact_set('h3-nvfp4'))
    rt.torch.cuda.get_device_name = lambda i: 'NVIDIA GeForce RTX 5090'
    pruned.check_gpu(rt, get_artifact_set('h3-nvfp4'))


def test_pruned_api_defaults_and_effective_geometry():
    from h3server.remote.app import GenerationInput
    r = GenerationInput(prompt='a', profile=PRUNED_PROFILE_NAMES[0], duration=5.18).to_request()
    assert r.settings.steps == 20 and r.settings.num_frames == 124
    assert GenerationInput(prompt='a').to_request().settings.steps == 50
