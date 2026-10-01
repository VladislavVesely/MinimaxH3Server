import io
import os
import subprocess
from types import SimpleNamespace
import pytest


def test_extract_rejects_missing_audio_failures_and_timeout(monkeypatch):
    from h3server.audio_export import extract, NoAudio
    for code,error in [(3,NoAudio),(1,ValueError)]:
        monkeypatch.setattr(subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=code,stdout=b'',stderr=b''))
        with pytest.raises(error):extract(42)
    def timeout(*a,**kw):raise subprocess.TimeoutExpired('fixture',25)
    monkeypatch.setattr(subprocess,'run',timeout)
    with pytest.raises(TimeoutError):extract(42)


def test_output_buffer_and_child_payload_are_bounded(monkeypatch):
    from h3server.audio_export import BoundedBuffer, MAX_MP3_BYTES, extract
    data=BoundedBuffer();data.seek(MAX_MP3_BYTES)
    with pytest.raises(ValueError):data.write(b'x')
    for payload in (b'not mp3',b'ID3'+b'x'*MAX_MP3_BYTES):
        monkeypatch.setattr(subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=payload,stderr=b''))
        with pytest.raises(ValueError):extract(42)


def test_audio_api_auth_stream_and_no_audio(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from test_remote_api import service,HEADERS
    from h3server import audio_export
    _,worker,app=service(tmp_path);client=TestClient(app)
    url='/api/v1/jobs/'+'a'*32+'/audio'
    assert client.get(url).status_code==401
    assert client.get(url,headers=HEADERS).status_code==404
    path=tmp_path/'fixture';path.write_bytes(b'original');fds=[]
    def opened(id):fds.append(os.open(path,os.O_RDONLY));return fds[-1]
    monkeypatch.setattr(worker,'open_video',opened)
    monkeypatch.setattr(audio_export,'extract',lambda fd:b'ID3fixture')
    response=client.get(url,headers=HEADERS)
    assert response.status_code==200 and response.content==b'ID3fixture'
    assert response.headers['content-type']=='audio/mpeg'
    assert response.headers['content-disposition'].endswith('.mp3"')
    assert response.headers['cache-control']=='no-store'
    with pytest.raises(OSError):os.fstat(fds[-1])
    def missing(fd):raise audio_export.NoAudio()
    monkeypatch.setattr(audio_export,'extract',missing)
    response=client.get(url,headers=HEADERS)
    assert response.status_code==422 and response.json()['detail']=='audio_track_missing'
    assert path.read_bytes()==b'original'


def test_audio_proxy_allowlist():
    from h3server.controller.proxy import RemoteProxy
    from h3server.controller.ssh import ControllerError
    from threading import RLock
    def denied():raise ControllerError('authentication_required')
    proxy=RemoteProxy(SimpleNamespace(lock=RLock(),_require_authentication=denied))
    with pytest.raises(ControllerError) as error:proxy.request('GET','/api/v1/jobs/'+'a'*32+'/audio')
    assert error.value.code=='authentication_required'
    with pytest.raises(ControllerError) as error:proxy.request('GET','/api/v1/jobs/'+'a'*32+'/audio?path=/etc/passwd')
    assert error.value.code=='proxy_route_forbidden'


def test_audio_concurrency(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from fastapi.testclient import TestClient
    from test_remote_api import service,HEADERS
    from h3server import audio_export
    _,worker,app=service(tmp_path);entered=Event();release=Event()
    path=tmp_path/'fixture';path.write_bytes(b'fixture')
    monkeypatch.setattr(worker,'open_video',lambda id:os.open(path,os.O_RDONLY))
    def held(fd):entered.set();assert release.wait(5);return b'ID3fixture'
    monkeypatch.setattr(audio_export,'extract',held)
    url='/api/v1/jobs/'+'a'*32+'/audio'
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(lambda:TestClient(app).get(url,headers=HEADERS))
        assert entered.wait(2)
        try:
            r=TestClient(app).get(url,headers=HEADERS)
            assert r.status_code==409 and r.json()['detail']=='audio_export_busy'
        finally:release.set()
        assert first.result().status_code==200


def test_actual_mp3_keeps_duration_and_original(tmp_path):
    av=pytest.importorskip('av');np=pytest.importorskip('numpy')
    from h3server.audio_export import extract
    path=tmp_path/'audio.mp4'
    with av.open(str(path),'w') as output:
        audio=output.add_stream('aac',rate=48000);audio.layout='stereo'
        for i in range(48):
            samples=np.sin((np.arange(1000)+i*1000)*2*np.pi*440/48000).astype('float32')*.1
            frame=av.AudioFrame.from_ndarray(np.stack([samples,samples]),format='fltp',layout='stereo');frame.sample_rate=48000
            for packet in audio.encode(frame):output.mux(packet)
        for packet in audio.encode(None):output.mux(packet)
    original=path.read_bytes()
    with path.open('rb') as f:data=extract(f.fileno())
    with av.open(io.BytesIO(data),format='mp3') as result:
        assert len(result.streams.audio)==1 and not result.streams.video
        duration=sum(frame.samples/frame.sample_rate for frame in result.decode(audio=0))
        assert .98<=duration<=1.1
    assert path.read_bytes()==original and list(tmp_path.iterdir())==[path]
