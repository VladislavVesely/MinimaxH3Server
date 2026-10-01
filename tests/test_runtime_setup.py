from types import SimpleNamespace
from dataclasses import asdict
import threading
import pytest
from h3server.paths import AppPaths
from h3server.backends.base import regular_file, atomic_json
from h3server.installer.planner import Inspection, ReleaseArtifact

Q5='h3-gguf-q5-k-m'


def test_full_runtime_is_provisioned_without_service_switch(tmp_path,monkeypatch):
    from h3server.remote.runtime_setup import RuntimeSetup
    from h3server.remote.runtimes import RuntimeBindings
    import h3server.remote.runtime_setup as m
    paths=AppPaths(tmp_path);calls=[]
    monkeypatch.setattr(m,'inspect_prerequisites',lambda:Inspection('ubuntu','26.04','x86_64',True,True,True,'disabled',False,True,None,(),(),()))
    artifact=ReleaseArtifact('sha256-fixture','h3server-0.1-py3-none-any.whl','a'*64)
    class Steps:
        def __init__(self,paths,plan,digest,io,log):
            calls.append(('plan',plan));self.directory=paths.environments/digest;self.directory.mkdir(exist_ok=True)
        def environment(self):calls.append('environment')
        def dependencies(self):calls.append('dependencies')
        def verify(self):calls.append('verify')
    monkeypatch.setattr(m,'Steps',Steps)
    monkeypatch.setattr(m,'artifact_path',lambda *a:tmp_path/'wheel')
    bindings=RuntimeBindings(paths)
    monkeypatch.setattr(bindings,'publish',lambda p,d,**kw:calls.append('publish') or {'python':str(paths.environments/d/'bin/python')})
    setup=RuntimeSetup(paths,bindings)
    with regular_file(paths,'state/test.lock',flags=__import__('os').O_CREAT|__import__('os').O_RDWR,mode='r+b',create_parents=True) as guard:
        result=setup.ensure(Q5,asdict(artifact),guard.fileno(),threading.Event(),lambda **kw:None)
    assert calls[1:]==['environment','dependencies','verify','publish']
    assert calls[0][1]['profile']==Q5 and not calls[0][1]['privileged_required']
    assert result['python'].startswith(str(paths.environments))
    assert not (paths.root/'config/active-release.json').exists()


def test_failed_environment_never_becomes_a_binding(tmp_path,monkeypatch):
    from h3server.remote.runtime_setup import RuntimeSetup
    from h3server.remote.runtimes import RuntimeBindings
    import h3server.remote.runtime_setup as m
    paths=AppPaths(tmp_path)
    monkeypatch.setattr(m,'inspect_prerequisites',lambda:Inspection('ubuntu','26.04','x86_64',True,True,True,'disabled',False,True,None,(),(),()))
    monkeypatch.setattr(m,'artifact_path',lambda *a:None)
    class Steps:
        def __init__(self,*a):pass
        def environment(self):pass
        def dependencies(self):raise RuntimeError('fixture dependency failure')
        def verify(self):raise AssertionError('must not verify')
    monkeypatch.setattr(m,'Steps',Steps)
    bindings=RuntimeBindings(paths)
    with pytest.raises(RuntimeError,match='fixture dependency'):
        RuntimeSetup(paths,bindings).ensure(Q5,asdict(ReleaseArtifact('fixture','h3server-0.1-py3-none-any.whl','a'*64)),None,threading.Event(),lambda **kw:None)
    assert bindings.resolve(Q5) is None
