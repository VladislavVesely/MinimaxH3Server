import json
import subprocess
import sys
from pathlib import Path
from test_controller_security import controller, bootstrap, API


def test_inventory_feature_exists():
    import importlib.util
    assert importlib.util.find_spec('h3server.controller.software_inventory') is not None


def collect(root):
    from h3server.controller.software_inventory import SOURCE
    return json.loads(subprocess.check_output([sys.executable,'-I','-S','-c',SOURCE,str(root)],timeout=25))


def fixture(root):
    env=root/'environments'/('a'*64);env.mkdir(parents=True)
    (root/'config').mkdir()
    (root/'config'/('installation-'+'a'*64+'.json')).write_text(json.dumps({'profile':'test-model','environment':str(env)}))
    packages=env/'lib/python3.14/site-packages';packages.mkdir(parents=True)
    meta=packages/'torch-2.14.0.dist-info';meta.mkdir();(meta/'METADATA').write_text('Name: torch\nVersion: 2.14.0+cu130\n\nSecret URL: NEVER_EXPORT\n')
    (env/'pyvenv.cfg').write_text('version = 3.14.4\n')
    return env,packages


def test_actual_metadata_not_freeze_or_imports(tmp_path):
    env,packages=fixture(tmp_path)
    (env/'resolved.txt').write_text('torch==0.0.1\ncredential=NEVER_EXPORT')
    (packages/'torch.py').write_text('raise RuntimeError("must not import")')
    result=collect(tmp_path)
    assert result['environments'][0]['packages']==[{'name':'torch','version':'2.14.0+cu130'}]
    assert result['environments'][0]['python_recorded']=='3.14.4'
    assert 'NEVER_EXPORT' not in json.dumps(result)


def test_no_follow_metadata_and_unregistered_environments(tmp_path):
    env,packages=fixture(tmp_path)
    foreign=tmp_path/'secret';foreign.write_text('Name: leaked\nVersion: 9.0\n')
    (packages/'torch-2.14.0.dist-info/METADATA').unlink()
    (packages/'torch-2.14.0.dist-info/METADATA').symlink_to(foreign)
    unregistered=tmp_path/'environments'/('b'*64);unregistered.mkdir()
    result=collect(tmp_path)
    assert len(result['environments'])==1
    assert result['environments'][0]['packages']==[]
    assert result['issues']


def test_crlf_and_group_writable_metadata_are_observations_not_trust_receipts(tmp_path):
    env,packages=fixture(tmp_path)
    meta=packages/'torch-2.14.0.dist-info/METADATA'
    meta.write_bytes(b'Name: torch\r\nVersion: 2.14.0\r\n\r\n')
    meta.chmod(0o664);meta.parent.chmod(0o775)
    assert collect(tmp_path)['environments'][0]['packages']==[{'name':'torch','version':'2.14.0'}]


def test_inventory_route_is_authenticated_and_target_bound(controller,monkeypatch):
    app,client,network=controller
    assert client.get('/assets/software.js').status_code==200
    assert client.get(API+'/software').status_code==401
    bootstrap(app,client)
    called=[]
    monkeypatch.setattr(app.state.connection,'remote_home',lambda:'/target')
    def observe(connection,home):
        called.append((connection,home));return {'environments':[],'issues':[]}
    monkeypatch.setattr('h3server.controller.software_inventory.observe',observe)
    assert client.get(API+'/software',headers={'origin':'https://evil.example'}).status_code==403
    assert not called
    assert client.get(API+'/software').status_code==200
    assert called==[(app.state.connection,'/target')]
    assert not network.sockets


def test_empty_target_does_not_create_installation(tmp_path):
    root=tmp_path/'missing'
    result=collect(root)
    assert result['environments']==[] and not root.exists()
