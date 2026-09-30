import io,os,struct,zlib
import pytest


def make_video(path):
    import av,numpy as np
    with av.open(str(path),'w') as c:
        stream=c.add_stream('libx264',rate=24);stream.width=64;stream.height=32;stream.pix_fmt='yuv420p'
        for color in [(180,20,30),(20,180,30),(20,30,180)]:
            pixels=np.empty((32,64,3),dtype=np.uint8);pixels[:]=color
            for packet in stream.encode(av.VideoFrame.from_ndarray(pixels,format='rgb24')):c.mux(packet)
        for packet in stream.encode():c.mux(packet)


def test_full_resolution_last_decoded_frame_no_png_compression(tmp_path):
    av=pytest.importorskip('av');np=pytest.importorskip('numpy')
    from PIL import Image
    from h3server.last_frame import extract
    path=tmp_path/'fixture.mp4';make_video(path)
    with av.open(str(path)) as c:expected=list(c.decode(video=0))[-1].to_ndarray(format='rgb24')
    with path.open('rb') as f:data=extract(f.fileno())
    image=Image.open(io.BytesIO(data));assert image.size==(64,32)
    assert np.array_equal(np.asarray(image),expected)
    offset=8;idat=b''
    while offset<len(data):
        size=struct.unpack('>I',data[offset:offset+4])[0];kind=data[offset+4:offset+8]
        if kind==b'IDAT':idat+=data[offset+8:offset+8+size]
        offset+=12+size
    # Level zero uses stored DEFLATE blocks, not lossy or compressed pixel data.
    stream=idat[2:-4];offset=0
    while True:
        header=stream[offset];assert (header>>1)&3==0;offset+=1
        size,inverse=struct.unpack('<HH',stream[offset:offset+4]);assert size^inverse==65535
        offset+=4+size
        if header&1:break
    assert offset==len(stream) and zlib.decompress(idat)
    assert list(tmp_path.iterdir())==[path]


def test_bad_media_and_timeout_do_not_return_images(tmp_path,monkeypatch):
    import subprocess
    from h3server.last_frame import extract
    path=tmp_path/'bad';path.write_bytes(b'not video')
    with path.open('rb') as f:
        with pytest.raises(ValueError):extract(f.fileno())
        def timeout(*a,**kw):raise subprocess.TimeoutExpired('fixture',25)
        monkeypatch.setattr(subprocess,'run',timeout)
        with pytest.raises(TimeoutError):extract(f.fileno())


def test_export_api_auth_and_expired_output(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from test_remote_api import service,HEADERS
    store,worker,app=service(tmp_path);client=TestClient(app)
    url='/api/v1/jobs/'+'a'*32+'/last-frame'
    assert client.get(url).status_code==401
    assert client.get(url,headers=HEADERS).status_code==404
    from h3server import last_frame
    path=tmp_path/'fixture.mp4';path.write_bytes(b'fixture')
    monkeypatch.setattr(last_frame,'extract',lambda fd:b'\x89PNG\r\n\x1a\nfixture')
    monkeypatch.setattr(worker,'open_video',lambda id:os.open(path,os.O_RDONLY))
    r=client.get(url,headers=HEADERS)
    assert r.status_code==200 and r.headers['content-type']=='image/png'
    assert 'last-frame.png' in r.headers['content-disposition'] and r.content.startswith(b'\x89PNG')
    def expired(id):raise ValueError('fixture expired')
    monkeypatch.setattr(worker,'open_video',expired)
    assert client.get(url,headers=HEADERS).status_code==410


def test_high_precision_is_never_silently_reduced(monkeypatch):
    import sys
    from types import SimpleNamespace as NS
    from contextlib import nullcontext
    from h3server.last_frame import decode
    video=NS(width=64,height=32,codec_context=NS(thread_count=0))
    frame=NS(width=64,height=32,format=NS(components=[NS(bits=10)]))
    container=NS(streams=NS(video=[video]),decode=lambda _:iter([frame]))
    monkeypatch.setitem(sys.modules,'av',NS(open=lambda *a,**kw:nullcontext(container)))
    with pytest.raises(ArithmeticError):decode(io.BytesIO(b'fixture'))


def test_concurrent_export_is_bounded(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from fastapi.testclient import TestClient
    from test_remote_api import service,HEADERS
    from h3server import last_frame
    _,worker,app=service(tmp_path);entered=Event();release=Event()
    path=tmp_path/'fixture';path.write_bytes(b'fixture')
    monkeypatch.setattr(worker,'open_video',lambda id:os.open(path,os.O_RDONLY))
    def held(fd):entered.set();assert release.wait(5);return b'\x89PNG\r\n\x1a\nfixture'
    monkeypatch.setattr(last_frame,'extract',held)
    url='/api/v1/jobs/'+'a'*32+'/last-frame'
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(lambda:TestClient(app).get(url,headers=HEADERS))
        assert entered.wait(2)
        try:
            r=TestClient(app).get(url,headers=HEADERS)
            assert r.status_code==409 and r.json()['detail']=='last_frame_export_busy'
        finally:release.set()
        assert first.result().status_code==200
