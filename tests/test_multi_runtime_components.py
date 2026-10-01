from types import SimpleNamespace
from dataclasses import replace
from test_components import components, apply, A

Q5='h3-gguf-q5-k-m'


def test_missing_gguf_runtime_is_selectable_and_provisioned_before_weights(components,monkeypatch):
    f=components;events=[];bindings={}
    row=next(i for i in f.sync.inventory()['items'] if i['id']==Q5)
    assert row['can_install'], 'A missing runtime must be provisioned, not disable the model'
    library=f.sync.library
    monkeypatch.setattr(library.runtimes,'resolve',lambda name:bindings.get(name))
    def ensure(profile,artifact,fd,stop,publish):
        assert f.worker.maintenance_status()['active']
        events.append('provision')
        bindings[profile]={'python':'/fixture/gguf/bin/python','environment':'b'*64,'artifact':{}}
        return bindings[profile]
    monkeypatch.setattr(library.runtime_setup,'ensure',ensure)
    import h3server.remote.model_library as m
    import h3server.backends.pruned as pruned
    monkeypatch.setattr(pruned,'backend',lambda paths,name,**kw:replace(library.base,profiles=(name,),command=(kw.get('python','fixture'),name)))
    def download(paths,**kw):events.append('weights');f.put(kw['profile_name'])
    monkeypatch.setattr(m,'download_model',download)
    result=apply(f,[A,Q5])
    assert result['state']=='completed',result
    assert events==['provision','weights']
    assert set(f.worker.backend.profiles)=={A,Q5}
    assert f.worker.backend.command_for_profile(Q5)[0]=='/fixture/gguf/bin/python'
    assert (f.root/next(iter(f.files(A)))).exists()


def test_failed_runtime_preserves_existing_model_and_does_not_download(components,monkeypatch):
    f=components
    def fail(*a,**kw):raise ValueError('fixture runtime provisioning failure')
    monkeypatch.setattr(f.sync.library.runtime_setup,'ensure',fail)
    state=apply(f,[Q5])
    assert state['state']=='failed' and 'provisioning failure' in state['error']
    assert (f.root/next(iter(f.files(A)))).exists()
    assert not (f.root/next(iter(f.files(Q5)))).exists()
