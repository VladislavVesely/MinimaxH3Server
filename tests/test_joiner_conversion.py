import subprocess
import pytest
from test_joiner import clips
from h3server.controller.joiner import Joiner, JoinerError, probe, tool


def test_incompatible_conversion_is_explicit_and_keeps_originals(clips):
    j=Joiner();before=[p.read_bytes() for p in clips]
    try:
        a=j.add(before[0]);b=j.add(before[2]);ids=[a['id'],b['id']]
        with pytest.raises(JoinerError,match='incompatible'):j.export(ids)
        assert not j.exports
        result=j.export(ids,convert=True)
        assert result['converted'] is True
        info=probe(j.path(result['id'],export=True),tool('ffprobe'))
        assert (info['streams'][0]['width'],info['streams'][0]['height'])==(96,64)
        assert info['streams'][0]['codec_name']=='h264'
        assert info['streams'][1]['codec_name']=='aac'
        assert abs(info['duration']-2)<.2
        pixels=subprocess.check_output([tool('ffmpeg'),'-v','error','-ss','1.5','-i',str(j.path(result['id'],export=True)),
                                        '-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-'])
        assert max(pixels[:3])<12  # black padding, not stretching/cropping
        center=(32*96+48)*3
        assert pixels[center+1]>80 and pixels[center]<20  # second (green) clip retained in order
        assert [p.read_bytes() for p in clips]==before
        assert len(j.clips)==2
    finally:j.close()


def test_compatible_stays_stream_copy_even_with_permission(clips):
    j=Joiner()
    try:
        a=j.add(clips[0].read_bytes());b=j.add(clips[1].read_bytes())
        result=j.export([a['id'],b['id']],convert=True)
        assert result.get('converted',False) is False
    finally:j.close()


def test_conversion_supplies_silence_for_missing_audio(clips,tmp_path):
    path=tmp_path/'silent.mp4'
    subprocess.run([tool('ffmpeg'),'-v','error','-i',str(clips[2]),'-c:v','copy','-an',str(path)],check=True)
    j=Joiner()
    try:
        a=j.add(path.read_bytes());b=j.add(clips[0].read_bytes())
        result=j.export([a['id'],b['id']],convert=True)
        assert result['audio'] is True and result['converted'] is True
        assert abs(result['duration']-2)<.2
    finally:j.close()
