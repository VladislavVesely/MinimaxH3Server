from contextlib import nullcontext
from dataclasses import asdict
import json
import sys
from types import SimpleNamespace as NS

import pytest

from h3server.backends import h3
from h3server.domain import GenerationSettings, H3_PROFILES, JobRequest
from h3server.paths import AppPaths


def test_import_does_not_import_inference_dependencies():
    import subprocess
    code = "import sys; import h3server.backends.h3; assert not any(x in sys.modules for x in ('torch','diffusers','transformers','av'))"
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.parametrize("field,value", [("negative_prompt", "no"), ("guidance_scale", 5), ("callback_on_step_end", "x"), ("sparse_attention", False)])
def test_strict_request_rejects_ignored_inputs(field, value):
    request = asdict(JobRequest("prompt", settings=GenerationSettings()))
    request[field] = value
    with pytest.raises(ValueError):
        h3.parse_request(request)


@pytest.mark.parametrize("loras", [["../evil"], ["a", "a"], ["a", "b"]])
def test_lora_request_limits(loras):
    request = asdict(JobRequest("prompt", settings=GenerationSettings()))
    request["loras"] = loras
    with pytest.raises(ValueError):
        h3.parse_request(request)


def test_int8_lora_gated():
    request = asdict(JobRequest("prompt", ("adapter",), settings=GenerationSettings(profile=H3_PROFILES[1])))
    with pytest.raises(ValueError, match="int8"):
        h3.parse_request(request)


class Model:
    def __init__(self):
        self.model = object()
        self.calls = []
    def requires_grad_(self, value): self.calls.append(("freeze", value))
    def enable_group_offload(self, **kwargs): self.calls.append(("offload", kwargs))
    def to(self, *args, **kwargs): self.calls.append(("to", args, kwargs))


class Pipe:
    def __init__(self):
        self._component_specs = {n: NS(pretrained_model_name_or_path="UNPINNED", revision=None, subfolder=n) for n in h3.REQUIRED}
        self.loads = []
        for name in h3.REQUIRED: setattr(self, name, None)
    def load_components(self, **kwargs):
        self.loads.append(kwargs)
        for n in kwargs["names"]: setattr(self, n, Model())
    def update_components(self, **kwargs):
        for n,v in kwargs.items(): setattr(self,n,v)


@pytest.fixture
def runtime():
    pipe, created, quantized, groups, manager_calls = Pipe(), [], [], [], []
    def construct(snapshot, **kwargs):
        created.append((snapshot, kwargs))
        return pipe
    def model_load(snapshot, **kwargs):
        quantized.append((snapshot, kwargs))
        return Model()
    manager = NS(enable_auto_cpu_offload=lambda **kw: manager_calls.append(kw))
    runtime = NS(torch=NS(bfloat16="bf16", device=lambda v:v),
                 ModularPipeline=NS(from_pretrained=construct), ComponentsManager=lambda: manager,
                 Transformer=NS(from_pretrained=model_load), TextEncoder=NS(from_pretrained=model_load),
                 DiffusersTorchAoConfig=lambda *a, **k: ("diffusers", a, k),
                 TransformersTorchAoConfig=lambda *a, **k: ("transformers", a, k),
                 Int8WeightOnlyConfig=lambda **kw: kw,
                 apply_group_offloading=lambda *a, **kw: groups.append((a,kw)))
    return runtime, pipe, created, quantized, groups, manager_calls


@pytest.mark.parametrize("profile", H3_PROFILES)
def test_pinned_local_load_and_offload_contract(runtime, tmp_path, profile):
    rt, pipe, created, quantized, groups, manager = runtime
    assert h3.load_pipeline(tmp_path, profile, rt) is pipe
    assert created[0][1]["workflow"] == "t2va"
    assert created[0][1]["trust_remote_code"] is False
    assert created[0][1]["local_files_only"] is True
    for name, spec in pipe._component_specs.items():
        assert spec.pretrained_model_name_or_path == str(tmp_path)
        assert spec.revision == h3.load_manifest()["model"]["revision"]
    kw = pipe.loads[0]
    assert kw["local_files_only"] is True and kw["trust_remote_code"] is False
    assert kw["use_safetensors"] is True
    assert kw["subfolder"] == {n:n for n in kw["names"]}
    if profile == H3_PROFILES[0]:
        assert manager == [{"device":"cuda", "memory_reserve_margin":"12GB"}]
        assert not quantized
    else:
        assert len(quantized) == 2 and not manager
        assert quantized[0][1]["low_cpu_mem_usage"] is False
        assert quantized[0][1]["quantization_config"][0] == "diffusers"
        assert quantized[1][1]["quantization_config"][0] == "transformers"
        assert quantized[0][1]["quantization_config"][1] == ({"version":2},)
        assert groups[0][1]["offload_type"] == "leaf_level"
        assert groups[0][1]["use_stream"] is True
        assert ("freeze", False) in pipe.transformer.calls
        assert ("to", ("cuda",), {}) in pipe.vae.calls


def test_swallowed_load_failure_is_fatal(runtime, tmp_path):
    rt, pipe, *_ = runtime
    pipe.load_components = lambda **kw: None
    with pytest.raises(RuntimeError, match="components"):
        h3.load_pipeline(tmp_path, H3_PROFILES[0], rt)


def test_real_progress_factory_total_and_restoration():
    class Denoise:
        def progress_bar(self, **kw): return "original"
    class Encode:
        def __call__(self, *a): return "encoded"
    denoise = Denoise()
    old = Denoise.progress_bar
    old_encode = Encode.__call__
    rt = NS(Denoise=Denoise, stage_classes={"text_encoding": Encode})
    events = []
    with pytest.raises(RuntimeError):
        with h3.instrument_progress(rt, events.append):
            assert Encode()() == "encoded"
            with denoise.progress_bar(total=3) as bar:
                bar.update()
                bar.update()
                raise RuntimeError("failed step")
    assert Denoise.progress_bar is old and Encode.__call__ is old_encode
    steps = [e for e in events if e.stage == "denoising"]
    assert [(e.progress.completed,e.progress.total) for e in steps] == [(0,3),(1,3),(2,3),(2,3)]
    assert steps[-1].status == "failed"
    assert events[0].progress is None


def test_invocation_and_mux_validate_before_success(tmp_path):
    events, calls = [], []
    class Pipeline:
        def __call__(self, **kwargs):
            calls.append(kwargs)
            return {"videos":["frames"], "audio":["sound"], "sampling_rate":32000}
    rt = NS(torch=NS(Generator=lambda: NS(manual_seed=lambda seed: ("seed",seed)), inference_mode=nullcontext),
            encode_video=lambda *a, **k: calls.append((a,k)))
    output = tmp_path / "video.mp4"
    h3.generate(Pipeline(), JobRequest("prompt", settings=GenerationSettings()), output, rt,
                emit=events.append, validator=lambda path, settings: calls.append((path,settings)))
    assert calls[0] == {"prompt":"prompt", "num_frames":124, "width":1344, "height":768,
                        "num_inference_steps":50, "generator":("seed",42), "output":["videos","audio","sampling_rate"]}
    assert calls[1][1]["audio"] == "sound"
    assert calls[2][0] == output
    assert events[-1].stage == "validation" and events[-1].status == "completed"


def test_failed_media_validation_does_not_complete(tmp_path):
    rt = NS(torch=NS(Generator=lambda: NS(manual_seed=lambda seed:seed), inference_mode=nullcontext),
            encode_video=lambda *a, **k: None)
    events = []
    with pytest.raises(ValueError):
        h3.generate(lambda **kw: {"videos":[1],"audio":[2],"sampling_rate":32000},
                    JobRequest("p", settings=GenerationSettings()), tmp_path/"video.mp4", rt,
                    emit=events.append, validator=lambda *a: (_ for _ in ()).throw(ValueError("invalid")))
    assert events[-1].status == "failed"


def test_dependency_diagnostic_is_not_ready_on_import_alone(monkeypatch):
    monkeypatch.setattr(h3, "check_dependencies", lambda: ["torch version mismatch"])
    result = h3.runtime_diagnostic()
    assert result["environment_ready"] is False
    assert result["generation_tested"] is False
    assert result["issues"] == ["torch version mismatch"]


class Tensor:
    def __init__(self, shape=(2,4), value=1):
        self.shape, self.value = shape, value
        self.ndim, self.dtype, self.device = len(shape), 'bf16', 'cpu'
    def is_floating_point(self): return True
    def clone(self): return Tensor(self.shape, self.value)
    def to(self, **kw): return self
    def __imul__(self, scale):
        self.value *= scale
        return self


def lora_runtime():
    return NS(torch=NS(bfloat16='bf16',
        isfinite=lambda t: NS(all=lambda: NS(item=lambda: t.value != float('inf'))),
        count_nonzero=lambda t: NS(item=lambda: bool(t.value)),
        equal=lambda a,b: a.shape == b.shape and a.value == b.value))


@pytest.mark.parametrize('mode', ['installed', 'noop', 'inactive', 'wrong_weights'])
def test_lora_installation_is_checked_not_warning_assumed_success(mode):
    rt = lora_runtime()
    module = NS(weight=Tensor((8,4)), active_adapters=['h3_managed'])
    transformer = NS(get_submodule=lambda n: module, peft_config={}, to=lambda **kw: None)
    calls = []
    def load(state, **kwargs):
        calls.append((state,kwargs))
        if mode == 'noop': return
        transformer.peft_config['h3_managed'] = object()
        for side in ('A','B'):
            tensor = state[f'transformer.block.lora_{side}.weight'].clone()
            if mode == 'wrong_weights': tensor.value = 99
            setattr(module, 'lora_' + side, {'h3_managed':NS(weight=tensor)})
        if mode == 'inactive': module.active_adapters = []
    pipe = NS(transformer=transformer, load_lora_weights=load)
    tensors = {'transformer.block.lora_A.weight':Tensor(), 'transformer.block.lora_B.weight':Tensor((8,2))}
    if mode == 'installed':
        h3.install_lora(pipe, (tensors,4), rt)
        assert calls[0][0]['transformer.block.lora_B.weight'].value == 2
        assert tensors['transformer.block.lora_B.weight'].value == 1
        assert calls[0][1]['load_into_transformer_ref'] is False
        assert calls[0][1]['use_safetensors'] is True
    else:
        with pytest.raises(RuntimeError): h3.install_lora(pipe, (tensors,None), rt)


@pytest.mark.parametrize('problem', ['unpaired','shape','zero','nonfinite','dora','empty'])
def test_incompatible_lora_never_calls_upstream_loader(problem):
    rt = lora_runtime()
    tensors = {'transformer.block.lora_A.weight':Tensor(), 'transformer.block.lora_B.weight':Tensor((8,2))}
    if problem == 'unpaired': tensors.pop('transformer.block.lora_B.weight')
    if problem == 'shape': tensors['transformer.block.lora_B.weight'] = Tensor((9,2))
    if problem == 'zero': tensors['transformer.block.lora_B.weight'].value = 0
    if problem == 'nonfinite': tensors['transformer.block.lora_A.weight'].value = float('inf')
    if problem == 'dora': tensors['transformer.block.dora_scale'] = Tensor()
    if problem == 'empty': tensors = {}
    pipe = NS(transformer=NS(get_submodule=lambda n: NS(weight=Tensor((8,4)))),
              load_lora_weights=lambda *a, **k: pytest.fail('incompatible LoRA loaded'))
    with pytest.raises(ValueError): h3.install_lora(pipe, (tensors,None), rt)


@pytest.mark.parametrize('problem', [None, 'partition','revision','alpha','unknown_metadata','dora','empty'])
def test_managed_safetensors_metadata_contract(tmp_path, monkeypatch, problem):
    paths = AppPaths(tmp_path)
    paths.loras.mkdir()
    (paths.loras/'adapter.safetensors').write_bytes(b'boundary fixture, not actual weights')
    meta = {'h3_partition':'FL2VA','h3_model_revision':h3.load_manifest()['model']['revision']}
    keys = ['transformer.block.lora_A.weight','transformer.block.lora_B.weight']
    if problem == 'partition': meta['h3_partition'] = 'Ref2VA'
    if problem == 'revision': meta['h3_model_revision'] = 'main'
    if problem == 'alpha': meta['alpha'] = 'NaN'
    if problem == 'unknown_metadata': meta['rank_pattern'] = 'ignored upstream config'
    if problem == 'dora': keys.append('transformer.block.dora_scale')
    if problem == 'empty': keys = []
    def safe_open(path, **kw):
        assert path.startswith(('/dev/fd/', '/proc/self/fd/'))
        assert kw == {'framework':'pt','device':'cpu'}
        return nullcontext(NS(metadata=lambda:meta,keys=lambda:keys,get_tensor=lambda key:Tensor()))
    monkeypatch.setitem(sys.modules, 'safetensors', NS(safe_open=safe_open))
    if problem:
        with pytest.raises(ValueError): h3.read_lora(paths, 'adapter')
    else:
        tensors, alpha = h3.read_lora(paths,'adapter')
        assert set(tensors) == set(keys) and alpha is None


def test_worker_default_adapter_rejects_unavailable_lora_and_int8_combination(tmp_path):
    backend = h3.backend(AppPaths(tmp_path))
    with pytest.raises(ValueError):
        backend.validate_request(JobRequest('p', ('missing',), settings=GenerationSettings()))
    with pytest.raises(ValueError, match='int8'):
        backend.validate_request(JobRequest('p', ('adapter',), settings=GenerationSettings(profile=H3_PROFILES[1])))
    assert backend.readiness()
    assert backend.diagnostics()['generation_tested'] is False


@pytest.mark.parametrize('problem', [None, 'no_audio', 'short_video', 'short_audio', 'wrong_geometry', 'bad_fps'])
def test_media_validator_decodes_video_and_audio(monkeypatch, problem):
    settings = GenerationSettings()
    video = NS(type='video',width=1344,height=768,average_rate=24)
    audio = NS(type='audio',rate=32000,layout=NS(channels=[1,2]))
    if problem == 'bad_fps': video.average_rate = 30
    def demux(*streams):
        frames = settings.num_frames - 1 if problem == 'short_video' else settings.num_frames
        for _ in range(frames):
            frame = NS(width=1344,height=32 if problem == 'wrong_geometry' else 768)
            yield NS(stream=video,decode=lambda frame=frame:[frame])
        samples = 100 if problem == 'short_audio' else int(settings.duration*32000)
        yield NS(stream=audio,decode=lambda:[NS(samples=samples)])
    container = NS(format=NS(name='mov,mp4,m4a,3gp,3g2,mj2'),streams=NS(video=[video],audio=[] if problem == 'no_audio' else [audio]),demux=demux)
    monkeypatch.setitem(sys.modules,'av',NS(open=lambda *a, **k:nullcontext(container)))
    if problem:
        with pytest.raises(ValueError): h3._validate_container(object(),settings)
    else:
        h3._validate_container(object(),settings)


def test_document_rejects_unknown_request_and_path_before_dependencies(tmp_path, monkeypatch):
    paths = AppPaths(tmp_path)
    job_id = 'a'*32
    directory = paths.job_dir(job_id)
    directory.mkdir(parents=True)
    path = directory/'request.json'
    document = {'schema_version':1,'job_id':job_id,'root':str(paths.root),
                'output_path':str(directory/'video.mp4'),'request':asdict(JobRequest('p',settings=GenerationSettings()))}
    document['request']['negative_prompt'] = 'must not be ignored'
    path.write_text(json.dumps(document))
    monkeypatch.setattr(h3,'load_runtime',lambda:pytest.fail('imported before validation'))
    with pytest.raises(ValueError): h3.execute_document(path,emit=lambda e:None)
    document['request'].pop('negative_prompt')
    document['output_path'] = str(tmp_path/'outside.mp4')
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError): h3.execute_document(path,emit=lambda e:None)


def test_installed_dependency_preflight_checks_pin_source_and_pip(monkeypatch,tmp_path):
    import hashlib
    source = tmp_path/'pinned.py'
    source.write_bytes(b'pinned upstream source')
    manifest = h3.load_manifest()
    manifest['candidate_environment']['constraints'] = {'torch':'==2.14.0','huggingface-hub':'>=1.32.0,<2.0','diffusers':'git+pin'}
    for item in manifest['sources'].values():
        item.update(url='https://example/src/diffusers/pinned.py',sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    monkeypatch.setattr(h3,'load_manifest',lambda:manifest)
    versions = {'torch':'2.14.0','huggingface-hub':'1.32.0'}
    monkeypatch.setattr(h3.metadata,'version',lambda name:versions[name])
    pin = {'vcs_info':{'commit_id':manifest['backend']['revision']}}
    monkeypatch.setattr(h3.metadata,'distribution',lambda name:NS(read_text=lambda n:json.dumps(pin),locate_file=lambda p:source))
    checked = []
    monkeypatch.setattr(h3.subprocess,'run',lambda command,**kw: (checked.append(command) or NS(returncode=0)))
    assert h3.check_dependencies() == []
    assert checked == [[sys.executable,'-m','pip','check']]
    versions['huggingface-hub'] = '2.0.0'
    assert any('huggingface-hub' in issue for issue in h3.check_dependencies())
    versions['huggingface-hub'] = '1.32.0'
    pin['vcs_info']['commit_id'] = 'main'
    assert any('Git revision' in issue for issue in h3.check_dependencies())
    pin['vcs_info']['commit_id'] = manifest['backend']['revision']
    source.write_bytes(b'changed source')
    assert any('source mismatch' in issue for issue in h3.check_dependencies())


def test_source_contract_pinned_denoise_loop_drives_actual_timestep_total():
    # Pinned upstream denoise.py MiniMaxH3DenoiseLoopWrapper.__call__ loop:
    # https://github.com/huggingface/diffusers/blob/5ff8e59ff9fe81c6e2df4fb4c6ea0d97a5df5ab2/src/diffusers/modular_pipelines/minimax_h3/denoise.py#L263-L266
    class PinnedLoopExcerpt:
        def __call__(self, components, state):
            block_state = self.get_block_state(state)
            with self.progress_bar(total=len(block_state.timesteps)) as progress_bar:
                for i, t in enumerate(block_state.timesteps):
                    self.loop_step(components, block_state, i=i, t=t)
                    progress_bar.update()
        def get_block_state(self,state): return state
        def loop_step(self,*a,**kw): calls.append(kw)
        def progress_bar(self, **kw): pytest.fail('factory not instrumented')
    calls, events = [], []
    rt = NS(Denoise=PinnedLoopExcerpt,stage_classes={})
    with h3.instrument_progress(rt,events.append):
        PinnedLoopExcerpt()(object(),NS(timesteps=[9,6,3,1]))
    assert [call['t'] for call in calls] == [9,6,3,1]
    assert events[-1].status == 'completed'
    assert events[-1].progress.total == events[-1].progress.completed == 4
    assert [e.progress.completed for e in events if e.status == 'running'] == [0,1,2,3,4]


def test_health_exposes_separate_runtime_model_and_generation_diagnostics(tmp_path):
    from fastapi.testclient import TestClient
    from h3server.remote.app import create_app
    from h3server.remote.worker import Worker
    from h3server.store import Store
    paths = AppPaths(tmp_path)
    for path in paths.directories(): path.mkdir(parents=True,exist_ok=True)
    worker = Worker(Store(paths.state/'remote.sqlite'), paths)
    token = 'test-only-token-' + 'x'*32
    app = create_app(worker.store,worker,token=token)
    with TestClient(app) as client:
        health = client.get('/api/v1/health',headers={'Authorization':'Bearer '+token}).json()
    assert health['runtime_ready'] is False
    assert health['diagnostic']['environment_ready'] is False
    assert health['diagnostic']['model_ready'] is False
    assert health['diagnostic']['generation_tested'] is False


def test_failed_load_removes_partial_output_without_claiming_completion(tmp_path,monkeypatch):
    from h3server.backends.base import atomic_json
    paths = AppPaths(tmp_path)
    job_id = 'b'*32
    directory = paths.job_dir(job_id)
    directory.mkdir(parents=True)
    atomic_json(paths,f'tmp/jobs/{job_id}/request.json', {'schema_version':1,'job_id':job_id,'root':str(paths.root),
        'output_path':str(directory/'video.mp4'),'request':asdict(JobRequest('p',settings=GenerationSettings()))})
    monkeypatch.setattr(h3,'require_consent',lambda paths:None)
    monkeypatch.setattr(h3,'offline_environment',lambda paths:None)
    monkeypatch.setattr(h3,'verify_snapshot',lambda paths:(_ for _ in ()).throw(ValueError('bad hash')))
    monkeypatch.setattr(h3,'load_runtime',lambda:pytest.fail('unverified weights must not load'))
    events = []
    with pytest.raises(ValueError,match='bad hash'):
        h3.execute_document(directory/'request.json',emit=events.append)
    assert not (directory/'video.mp4').exists()
    assert [(e.stage,e.status) for e in events] == [('loading','running'),('loading','failed')]
