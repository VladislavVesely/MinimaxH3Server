import subprocess
import pytest
from test_joiner import clips


def test_missing_tools_never_installs_or_creates_workspace(monkeypatch):
    from h3server.controller import joiner
    monkeypatch.setattr(joiner,'tool',lambda name:None)
    j=joiner.Joiner()
    assert not j.status()['available']
    with pytest.raises(joiner.JoinerError,match='tools_missing'):j.add(b'1234ftyp')
    assert j._temporary is None
    j.close()


def test_overlong_timeline_is_rejected_before_export(clips):
    from h3server.controller.joiner import Joiner,JoinerError
    j=Joiner()
    try:
        a=j.add(clips[0].read_bytes());b=j.add(clips[1].read_bytes())
        for record in j.clips.values():record['duration']=1801
        with pytest.raises(JoinerError,match='duration_limit'):j.export([a['id'],b['id']])
        assert not j.exports
    finally:j.close()


def test_audio_layout_mismatch_is_not_silently_dropped(clips,tmp_path):
    from h3server.controller.joiner import Joiner,JoinerError
    silent=tmp_path/'silent.mp4'
    subprocess.run(['ffmpeg','-v','error','-i',str(clips[0]),'-an','-c:v','copy',str(silent)],check=True)
    j=Joiner()
    try:
        a=j.add(clips[0].read_bytes());b=j.add(silent.read_bytes())
        with pytest.raises(JoinerError,match='incompatible') as failure:j.export([a['id'],b['id']])
        assert 'audio/video track layout' in failure.value.fields
    finally:j.close()


def test_failed_export_cleans_partial_files_and_preserves_sources(clips,monkeypatch):
    from h3server.controller import joiner
    j=joiner.Joiner()
    try:
        a=j.add(clips[0].read_bytes());b=j.add(clips[1].read_bytes())
        original=joiner.run
        def fail(args,**kwargs):
            if '-c' in args:raise joiner.JoinerError('joiner_timeout',504)
            return original(args,**kwargs)
        monkeypatch.setattr(joiner,'run',fail)
        with pytest.raises(joiner.JoinerError,match='timeout'):j.export([a['id'],b['id']])
        assert not j.exports
        assert sorted(p.name for p in j._root().iterdir())==sorted([a['id']+'.mp4',b['id']+'.mp4'])
        assert j.path(a['id']).read_bytes()==clips[0].read_bytes()
    finally:j.close()


def test_export_rejects_changed_decoder_metadata(clips,monkeypatch):
    from h3server.controller import joiner
    j=joiner.Joiner()
    try:
        a=j.add(clips[0].read_bytes());b=j.add(clips[1].read_bytes())
        original=joiner.probe
        def changed(path,ffprobe):
            info=original(path,ffprobe)
            info['streams'][0]['color_transfer']='smpte2084'
            return info
        monkeypatch.setattr(joiner,'probe',changed)
        with pytest.raises(joiner.JoinerError,match='export_failed'):j.export([a['id'],b['id']])
        assert not j.exports
    finally:j.close()


def test_quota_and_symlinks_fail_closed(clips,monkeypatch,tmp_path):
    from h3server.controller import joiner
    j=joiner.Joiner()
    try:
        a=j.add(clips[0].read_bytes())
        monkeypatch.setattr(joiner,'MAX_WORKSPACE',1)
        with pytest.raises(joiner.JoinerError,match='storage_limit'):j.add(clips[1].read_bytes())
        path=j.path(a['id']);path.unlink();path.symlink_to(clips[0])
        with pytest.raises(joiner.JoinerError,match='missing'):j.path(a['id'])
    finally:j.close()
    assert clips[0].exists()
