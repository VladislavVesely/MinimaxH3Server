"""Focused real-codec acceptance; no inference or remote writes."""
import importlib.util
import json
import shutil
import subprocess

import pytest


def test_joiner_is_available():
    assert importlib.util.find_spec('h3server.controller.joiner'), 'Local lossless joiner is missing'


@pytest.fixture
def clips(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Local FFmpeg tools unavailable')
    result=[]
    for color,size in [('red','96x64'),('blue','96x64'),('green','128x64')]:
        p=tmp_path/(color+'.mp4')
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color={color}:size={size}:rate=12:duration=1',
                        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=1',
                        '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-ac','2','-shortest',str(p)],check=True,capture_output=True)
        result.append(p)
    return result


def packets(path):
    data=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_packets','-show_data_hash','sha256',
             '-show_entries','packet=stream_index,data_hash','-of','json',str(path)]))
    return [[p['data_hash'] for p in data['packets'] if p['stream_index']==i] for i in (0,1)]


def decoded_frames(path):
    output = subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-map','0:v:0',
                                     '-fps_mode','passthrough','-f','framemd5','-']).decode()
    return [line.rsplit(',',1)[1].strip() for line in output.splitlines() if not line.startswith('#')]


def test_stream_copy_preserves_original_packets_and_order(clips):
    from h3server.controller.joiner import Joiner
    j=Joiner()
    try:
        a=j.add(clips[0].read_bytes());b=j.add(clips[1].read_bytes())
        result=j.export([b['id'],a['id']]);path=j.path(result['id'],export=True)
        before=[p.read_bytes() for p in clips]
        actual=packets(path)
        for i in (0,1):assert actual[i]==packets(clips[1])[i]+packets(clips[0])[i]
        assert decoded_frames(path)==decoded_frames(clips[1])+decoded_frames(clips[0])
        assert [p.read_bytes() for p in clips]==before
        j.remove(a['id'])
        assert j.path(result['id'],export=True).exists()
        assert clips[0].exists()
    finally:j.close()
    assert not path.exists()


def test_incompatible_dimensions_and_unknown_ids_do_not_export(clips):
    from h3server.controller.joiner import Joiner, JoinerError
    j=Joiner()
    try:
        a=j.add(clips[0].read_bytes());b=j.add(clips[2].read_bytes())
        with pytest.raises(JoinerError,match='incompatible'):j.export([a['id'],b['id']])
        with pytest.raises(JoinerError,match='missing'):j.export([a['id'],'f'*32])
        with pytest.raises(JoinerError):j.path('../../etc/passwd')
        assert not j.exports
        j.clear();assert not j.clips
    finally:j.close()


def test_hevc_packet_copy(clips,tmp_path):
    from h3server.controller.joiner import Joiner
    if 'libx265' not in subprocess.check_output(['ffmpeg','-v','error','-encoders']).decode():
        pytest.skip('HEVC fixture encoder unavailable; export itself uses no encoder')
    hevc=tmp_path/'hevc.mp4'
    subprocess.run(['ffmpeg','-v','error','-i',str(clips[0]),'-c:v','libx265',
                    '-x265-params','log-level=error:pools=none','-threads','1',
                    '-tag:v','hvc1','-c:a','copy',str(hevc)],check=True)
    j=Joiner()
    try:
        a=j.add(hevc.read_bytes());result=j.export([a['id'],a['id']])
        actual=packets(j.path(result['id'],export=True));expected=packets(hevc)
        for i in (0,1):assert actual[i]==expected[i]*2
        assert decoded_frames(j.path(result['id'],export=True))==decoded_frames(hevc)*2
    finally:j.close()


def test_matching_rotation_is_preserved(clips,tmp_path):
    from h3server.controller.joiner import Joiner
    rotated=tmp_path/'rotated.mp4'
    subprocess.run(['ffmpeg','-v','error','-display_rotation:v:0','90','-i',str(clips[0]),
                    '-c','copy',str(rotated)],check=True)
    j=Joiner()
    try:
        a=j.add(rotated.read_bytes());b=j.add(rotated.read_bytes())
        result=j.export([a['id'],b['id']])
        assert j.exports[result['id']]['streams'][0]['side_data_list']==j.clips[a['id']]['streams'][0]['side_data_list']
    finally:j.close()


def test_invalid_upload_and_busy_operations_fail_closed():
    from h3server.controller.joiner import Joiner, JoinerError
    j=Joiner()
    try:
        with pytest.raises(JoinerError):j.add(b'not a video')
        assert not j.clips
        with j.lock:
            with pytest.raises(JoinerError,match='busy'):j.clear()
    finally:j.close()
