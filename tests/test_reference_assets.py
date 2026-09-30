"""Native conditioning contracts; fixtures never imply model quality."""
from dataclasses import asdict
import pytest
from h3server.domain import GenerationSettings
from h3server.remote.app import GenerationInput

ID='a'*64
@pytest.mark.parametrize('values', [dict(input_mode='frames',first_frame=ID),dict(input_mode='frames',last_frame=ID),dict(input_mode='references',reference_images=[ID]),dict(input_mode='references',reference_videos=[ID]),dict(input_mode='references',reference_audios=[ID])])
def test_conditioned_settings_roundtrip(values):
    request=GenerationInput(prompt='fixture',profile='h3-convrot-int8',**values).to_request()
    restored=GenerationSettings(**asdict(request.settings))
    assert restored==request.settings
    assert restored.input_mode==values['input_mode']

@pytest.mark.parametrize('values', [dict(input_mode='frames'),dict(first_frame=ID),dict(input_mode='references',first_frame=ID),dict(input_mode='frames',reference_images=[ID]),dict(input_mode='frames',first_frame='../escape'),dict(input_mode='references',reference_images=[ID]*10),dict(input_mode='references',reference_audios=ID),dict(input_mode='bad')])
def test_invalid_conditioning(values):
    with pytest.raises(ValueError):GenerationInput(prompt='fixture',profile='h3-convrot-int8',**values)

def test_unverified_sparse_combination_fails():
    with pytest.raises(ValueError):GenerationInput(prompt='fixture',profile='h3-convrot-int8',input_mode='frames',first_frame=ID,sparse_preset='quality')

@pytest.mark.parametrize('values', [dict(input_mode='references'),dict(input_mode='references',reference_images=[ID]),dict(input_mode='frames',first_frame=ID)])
def test_sage_turbo_conditioning_and_optional_references(values):
    request=GenerationInput(prompt='fixture',profile='h3-convrot-int8',sage_attention=True,turbo_lora=True,steps=6,**values).to_request()
    assert request.settings.sage_attention and request.settings.turbo_lora
    assert request.settings.input_mode==values['input_mode']
    from h3server.backends.pruned import parse_request
    assert parse_request(asdict(request))==request

@pytest.mark.parametrize('mode', ['frames','references'])
def test_runtime_conditions_with_assets_not_text_fallback(tmp_path,monkeypatch,mode):
    from test_pruned_runtime import runtime as fixture
    from h3server.backends import pruned,conditioning
    from types import SimpleNamespace as NS
    rt,calls=fixture.__wrapped__()
    captured={}
    def execute(**kw):captured.update(kw);return NS(result=('positive','latent'))
    if mode=='references':rt.reference_condition=NS(execute=execute);rt.reference_filename='Ref2VA.safetensors'
    else:rt.condition=NS(execute=execute)
    monkeypatch.setattr(conditioning,'load',lambda rt,s:dict(first_frame='first',last_frame='last') if mode=='frames' else dict(ref_images={'ref_image_1':'image'},ref_videos={'ref_video_1':'video'},ref_audios={'ref_audio_1':'audio'}))
    values=dict(input_mode=mode)
    if mode=='frames':values.update(first_frame=ID,last_frame=ID)
    else:values.update(reference_images=[ID],reference_videos=[ID],reference_audios=[ID])
    request=GenerationInput(prompt='fixture',profile='h3-convrot-int8',**values).to_request()
    pruned.generate(request,tmp_path/'video.mp4',rt,emit=lambda e:None)
    if mode=='frames':assert captured['first_frame']=='first' and captured['last_frame']=='last'
    else:
        assert captured['ref_images'] and captured['ref_videos'] and captured['ref_audios']
        assert captured['vae']=='vae' and captured['audio_vae']=='vae'
        assert any('Ref2VA.safetensors' in str(c) for c in calls)


def test_prompt_only_reference_still_uses_ref2va_and_turbo(tmp_path,monkeypatch):
    from test_pruned_runtime import runtime
    from h3server.backends import pruned,conditioning,turbo
    from types import SimpleNamespace as NS
    rt,calls=runtime.__wrapped__();captured={};rt.turbo=object();rt.reference_filename='Ref2VA.safetensors'
    def execute(**kw):captured.update(kw);return NS(result=('positive','latent'))
    rt.reference_condition=NS(execute=execute)
    monkeypatch.setattr(conditioning,'load',lambda rt,s:dict(ref_images={},ref_videos={},ref_audios={}))
    monkeypatch.setattr(turbo,'apply',lambda m,s,n:calls.append(('turbo_applied',s.turbo_lora,s.sage_attention)) or m)
    monkeypatch.setattr(turbo,'sample',lambda *args:calls.append(('turbo_sample',)) or 'sampled')
    r=GenerationInput(prompt='fixture',profile='h3-convrot-int8',input_mode='references',turbo_lora=True,sage_attention=True,steps=6).to_request()
    pruned.generate(r,tmp_path/'video.mp4',rt,emit=lambda e:None)
    assert captured['ref_images']=={} and captured['ref_videos']=={} and captured['ref_audios']=={}
    assert ('turbo_applied',True,True) in calls and ('turbo_sample',) in calls
    assert any('Ref2VA.safetensors' in str(c) for c in calls)


def test_performance_excludes_asset_ids_and_separates_modes(monkeypatch,tmp_path):
    from h3server.performance import configuration
    from h3server.assets import AssetStore
    from h3server.paths import AppPaths
    import json
    monkeypatch.setattr(AssetStore,'conditioning',lambda self,s:{'first_frame':[dict(width=512,height=288,frames=1,duration=0)]})
    request=GenerationInput(prompt='fixture',profile='h3-convrot-int8',input_mode='frames',first_frame=ID).to_request()
    config=configuration(request,AppPaths(tmp_path))
    assert config['input_mode']=='frames' and config['conditioning_shapes']['first_frame']==[[512,288,1,0]]
    assert ID not in json.dumps(config) and 'first_frame' not in config
    assert 'input_mode' not in configuration(GenerationInput(prompt='fixture').to_request())


def test_conditioned_estimates_predict_encoder_reuse(tmp_path,monkeypatch):
    from test_remote_api import service
    from h3server.assets import AssetStore
    _,worker,_=service(tmp_path)
    worker._hardware={'cpu_model':'fixture'};worker._resident_profile='h3-convrot-int8';worker._reuse_mode='warm'
    monkeypatch.setattr(worker,'models_status',lambda:{'loaded':True})
    monkeypatch.setattr(AssetStore,'conditioning',lambda *a:{})
    monkeypatch.setattr(worker.performance,'estimate',lambda c,h,mode:{'cache':mode})
    r=GenerationInput(prompt='fixture',profile='h3-convrot-int8',input_mode='frames',first_frame=ID).to_request()
    assert worker.estimate(r)['cache']=='encoder_reuse'
    worker._resident_input_mode='references'
    r=GenerationInput(prompt='fixture',profile='h3-convrot-int8').to_request()
    assert worker.estimate(r)['cache']=='encoder_reuse'


def test_reference_video_preserves_exact_24fps_samples(monkeypatch):
    import sys,io
    from types import SimpleNamespace as NS
    from contextlib import nullcontext
    from h3server.backends.conditioning import video
    frames=[]
    for i in range(5):
        frame=NS(time=i/24)
        frame.reformat=lambda i=i,**kw:NS(to_ndarray=lambda:i)
        frames.append(frame)
    container=NS(streams=NS(video=[NS(width=64,height=64,average_rate=24)]),decode=lambda s:iter(frames))
    monkeypatch.setitem(sys.modules,'av',NS(open=lambda *a,**k:nullcontext(container)))
    monkeypatch.setitem(sys.modules,'numpy',NS(stack=lambda a:a))
    class Tensor:
        def __init__(self,v):self.values=v
        def float(self):return self
        def div_(self,n):return self
    result=video(io.BytesIO(b'0000ftyp00000000'),NS(from_numpy=Tensor),GenerationSettings(width=64,height=64))
    assert result.values==list(range(5))


@pytest.mark.parametrize('extra',[{'first_frame':ID},{'conditioning_shapes':{'prompt':['private']}},{'conditioning_shapes':{'first_frame':[['private',1,1,0]]}}])
def test_timing_export_rejects_private_conditioning_fields(tmp_path,extra):
    from h3server.performance import PerformanceStore,configuration
    config=configuration(GenerationInput(prompt='fixture').to_request());config.update(extra)
    with pytest.raises(ValueError):PerformanceStore(tmp_path/'perf.sqlite').record('a'*32,config,{},dict(total_seconds=1,stages={},sampling_steps=[]),'cold',{'sage':False,'sparse':'off'})


def test_reference_install_requires_matching_digest_and_supported_profile(tmp_path):
    from test_remote_api import service,HEADERS
    from fastapi.testclient import TestClient
    from h3server.backends.reference import DIGEST
    _,_,app=service(tmp_path)
    with TestClient(app) as c:
        assert c.post('/api/v1/features/reference',json={'artifact_digest':DIGEST}).status_code==401
        assert c.post('/api/v1/features/reference',json={'artifact_digest':'0'*64},headers=HEADERS).status_code==409
        assert c.post('/api/v1/features/reference',json={'artifact_digest':DIGEST},headers=HEADERS).status_code==409
        assert c.get('/api/v1/features/reference',headers=HEADERS).json()['installed'] is False
    assert not (tmp_path/'config/reference-consent.json').exists()


def test_reference_requests_bind_the_checkpoint_digest():
    from dataclasses import replace
    from h3server.backends import pruned,reference
    from h3server.domain import JobRequest
    r=GenerationInput(prompt='fixture',profile='h3-convrot-int8',input_mode='references',reference_images=[ID]).to_request()
    assert r.settings.reference_artifact_digest==reference.DIGEST
    wrong=replace(r.settings,reference_artifact_digest='0'*64)
    with pytest.raises(ValueError,match='Ref2VA'):pruned.parse_request(asdict(JobRequest('fixture',settings=wrong)))


def test_ref_checkpoint_is_separate_and_pinned():
    from h3server.backends import reference
    assert reference.FILES[0]['sha256']=='ed7e9aa541b945f44298650f0dc2e2e867bfd3b67cd07bcc90f6081c168f0d3d'
    assert reference.TOTAL==20970379689
    assert '908eccad7e68751190d04c171956f163bfeed741' in reference.FILES[0]['url']
    assert 'Ref2VA' in reference.WEIGHT
    from h3server.backends.safetensors_compat import REPAIRS
    assert REPAIRS[reference.FILES[0]['sha256']]==(73,'9255f52b6677845ad238f20dfaafa94727053694127ab7f255c048f0f9365779')
