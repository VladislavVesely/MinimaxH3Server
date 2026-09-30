from contextlib import nullcontext
from types import SimpleNamespace as NS
import sys
import pytest
from h3server.domain import GenerationSettings
from h3server.backends.catalog import PRUNED_PROFILE_NAMES
from h3server.remote.app import GenerationInput
from h3server.backends import h3, pruned
from test_pruned_runtime import runtime

@pytest.mark.parametrize('profile', PRUNED_PROFILE_NAMES)
@pytest.mark.parametrize('size', [(1920,1088),(1088,1920),(1280,736),(864,480)])
def test_pruned_aligned_hd_input(profile,size):
    value=GenerationInput(prompt='fixture',profile=profile,width=size[0],height=size[1]).to_request()
    assert (value.settings.width,value.settings.height)==size

@pytest.mark.parametrize('size', [(1920,1080),(1920,1920),(1952,1024)])
def test_oversize_or_unaligned_canvas_stays_rejected(size):
    with pytest.raises(ValueError):GenerationSettings(profile='h3-convrot-int8',width=size[0],height=size[1])

@pytest.mark.parametrize('profile,maximum,pixels', [('h3-convrot-int8',1920,1920*1088),('h3-bf16-cpu-offload-experimental',1344,1344*768)])
def test_capabilities_advertise_profile_limits(tmp_path,profile,maximum,pixels):
    from fastapi.testclient import TestClient
    from test_remote_api import service,HEADERS
    from h3server.remote.worker import Backend
    _,_,app=service(tmp_path,backend=Backend(profiles=(profile,)),min_free_bytes=0)
    with TestClient(app) as client:
        data=client.get('/api/v1/capabilities',headers=HEADERS).json()
        assert data['service_limits']['axis_max']==maximum
        assert data['service_limits']['canvas_pixels']==pixels


def test_legacy_canvas_limit_unchanged():
    with pytest.raises(ValueError):GenerationSettings(width=1920,height=1088)

def test_pruned_node_io_passes_full_hd_without_resizing(runtime,tmp_path):
    rt,calls=runtime
    request=GenerationInput(prompt='fixture',profile='h3-convrot-int8',width=1920,height=1088).to_request()
    result=pruned.generate(request,tmp_path/'fixture.mp4',rt,emit=lambda e:None)
    condition=next(c for c in calls if c[0]=='condition')[2]
    assert (condition['width'],condition['height'])==(1920,1088)
    assert (result['width'],result['height'])==(1920,1088)


def test_parent_media_validation_uses_selected_profile(monkeypatch):
    settings=GenerationSettings(profile='h3-convrot-int8',width=1920,height=1088)
    video=NS(type='video',width=1920,height=1088,average_rate=24)
    audio=NS(type='audio',rate=32000,layout=NS(channels=[1,2]))
    def demux(*streams):
        for _ in range(124):yield NS(stream=video,decode=lambda:[NS(width=1920,height=1088)])
        yield NS(stream=audio,decode=lambda:[NS(samples=round(124/24*32000))])
    container=NS(format=NS(name='mp4'),streams=NS(video=[video],audio=[audio]),demux=demux)
    monkeypatch.setitem(sys.modules,'av',NS(open=lambda *a,**k:nullcontext(container)))
    h3._validate_container(object(),settings)
    h3._validate_container(object(),profile='h3-convrot-int8')
    with pytest.raises(ValueError):h3._validate_container(object())
