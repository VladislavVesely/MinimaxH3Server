import os
from pathlib import Path
import pytest
from h3server.controller.ssh import SSHConnection

pytestmark=pytest.mark.skipif(os.name!='posix',reason='Local target transport is POSIX-only')


def test_local_selection_does_not_connect_ssh(tmp_path):
    def forbidden(*a,**kw):pytest.fail('Local target must not use SSH or a network connection')
    connection=SSHConnection(tmp_path,socket_factory=forbidden,transport_factory=forbidden)
    state=connection.connect_local()
    assert state['state']=='authenticated' and state['mode']=='local'
    assert state['fingerprint'] is None
    assert Path(connection.remote_home()).is_dir()
    result=connection(('uname','-s'),timeout=5)
    assert result.succeeded and result.stdout.strip()==os.uname().sysname
    connection.close()


def test_local_file_adapter_confines_writes_and_rejects_links(tmp_path):
    from h3server.controller.local import LocalFiles
    root=tmp_path/'MinimaxH3Server';root.mkdir(mode=0o700)
    files=LocalFiles(root)
    with files.open(str(root/'receipt'),'wx') as f:f.write(b'private')
    assert (root/'receipt').stat().st_mode&0o077==0
    with pytest.raises(ValueError):files.open(str(tmp_path/'outside'),'wx')
    (root/'link').symlink_to(root/'receipt')
    with pytest.raises((ValueError,OSError)):files.open(str(root/'link'),'rb')
    os.link(root/'receipt',root/'alias')
    with pytest.raises(ValueError):files.open(str(root/'receipt'),'rb')
    outside=tmp_path/'outside-dir';outside.mkdir()
    (root/'escape').symlink_to(outside,target_is_directory=True)
    with pytest.raises(OSError):files.open(str(root/'escape'/'file'),'wx')
    assert not (outside/'file').exists()
    files.close()


def test_local_commands_have_deadlines_and_guardians_survive_eof(tmp_path):
    import shlex,sys,time
    from h3server.controller.local import LocalChannel
    connection=SSHConnection(tmp_path/'trust');connection.connect_local()
    cwd=connection._execute(shlex.join([sys.executable,'-c','import os;print(os.getcwd())']),5)
    assert cwd.stdout.strip()=='/', 'Caller working directories must not shadow pinned helper modules'
    result=connection._execute(shlex.join([sys.executable,'-c','import time;time.sleep(30)']),.15)
    assert result.error=='timeout' and connection.status()['state']=='disconnected'
    marker=tmp_path/'finished'
    channel=LocalChannel(guardian=True)
    script='import time,pathlib; print("ready",flush=True);time.sleep(.2);pathlib.Path('+repr(str(marker))+').write_text("finished")'
    channel.exec_command(shlex.join([sys.executable,'-c',script]))
    assert channel.recv(100).strip()==b'ready'
    channel.close()
    assert channel.exit_status_ready(), 'Controller must observe EOF immediately while the guardian retains ownership'
    assert channel.recv_exit_status()==-1
    channel.process.wait(timeout=3)
    assert marker.read_text()=='finished'


def test_local_api_scan_and_unsupported_install(tmp_path,monkeypatch):
    import platform
    from fastapi.testclient import TestClient
    from h3server.controller.app import create_app,API
    from test_controller_security import bootstrap,ORIGIN
    app=create_app(state_dir=tmp_path,origin=ORIGIN)
    with TestClient(app,base_url=ORIGIN) as client:
        headers=bootstrap(app,client)
        assert client.post(API+'/connection/local',json={}).status_code==403
        response=client.post(API+'/connection/local',headers=headers,json={})
        assert response.status_code==200 and response.json()['mode']=='local'
        assert client.post(API+'/connection/local',headers=headers,json={}).status_code==409
        if platform.system()=='Darwin':
            result=client.post(API+'/scan',headers=headers,json={})
            assert result.status_code==200 and result.json()['probe']['hardware']['distribution']=='macos'
            assert result.json()['probe']['hardware']['ram_bytes']>0
            monkeypatch.setattr(app.state.connection,'check_setup_password',lambda *a:pytest.fail('must not run sudo on unsupported macOS'))
            assert client.post(API+'/connection/local/authorize',headers=headers,json={'password':'fixture'}).status_code==409
        app.state.connection._local_platform='Linux'  # controlled authorization boundary only
        checked=[]
        monkeypatch.setattr(app.state.connection,'check_setup_password',lambda password:checked.append(password))
        result=client.post(API+'/connection/local/authorize',headers=headers,json={'password':'fixture-local-secret'})
        assert result.status_code==200 and result.json()['recovery']['setup_credential_retained']
        assert checked==['fixture-local-secret'] and 'fixture-local-secret' not in result.text
        assert not (tmp_path/'preferences.json').exists(), 'Local sudo authorization must not save a password'
