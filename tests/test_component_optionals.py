import os
import pytest
from h3server.backends.base import regular_file,atomic_json
from test_components import components,apply,A,B

@pytest.mark.parametrize('name',['turbo','sparse','reference'])
def test_optional_add_remove_uses_parent_guard_and_preserves_other_assets(components,monkeypatch,name):
    f=components;module=f.module.MODULES[name];installer=f.sync.installers[name]
    target=f'components/fixture-{name}/weights.bin'
    original=f.module.optional_files
    monkeypatch.setattr(f.module,'optional_files',lambda n:{target:(3,'a'*64)} if n==name else original(n))
    monkeypatch.setattr(installer,'status',lambda:dict(installed=(f.root/target).exists(),busy=False))
    monkeypatch.setattr(module,'installed',lambda *args,**kwargs:(f.root/target).exists())
    def run(operation,*,release,maintenance_fd):
        assert release is False and os.fstat(maintenance_fd).st_uid==os.getuid()
        assert f.worker.maintenance_status()['active']
        with regular_file(f.worker.paths,target,flags=os.O_CREAT|os.O_WRONLY,mode='wb',create_parents=True) as stream:stream.write(b'abc')
        atomic_json(f.worker.paths,module.RECEIPT,module.BINDING)
        installer._publish(state='installed')
    monkeypatch.setattr(installer,'_run',run)
    assert apply(f,[A,name])['state']=='completed'
    keep=f.root/'assets';keep.mkdir();(keep/'reference-input').write_bytes(b'original')
    assert apply(f,[A])['state']=='completed'
    assert not (f.root/target).exists() and not (f.root/module.RECEIPT).exists()
    assert (keep/'reference-input').read_bytes()==b'original'


@pytest.mark.parametrize('frozen',[False,True])
def test_sage_uninstall_preserves_frozen_dependencies_and_invalidates_receipt(components,monkeypatch,frozen):
    import sys
    import h3server.backends.comfy_runtime as runtime
    import h3server.installer.steps as steps
    f=components;installer=f.sync.installers['sageattention'];receipt=f.module.sage_attention.RECEIPT
    env=f.root/'environments'/('a'*64);env.mkdir(parents=True)
    (env/'resolved.txt').write_text('sageattention==1.0.6\n' if frozen else 'torch==fixture\n')
    monkeypatch.setattr(sys,'prefix',str(env))
    monkeypatch.setattr(runtime,'verify_provisioned',lambda *args,**kwargs:env)
    monkeypatch.setattr(installer,'status',lambda:dict(installed=(f.root/receipt).exists(),busy=False))
    atomic_json(f.worker.paths,receipt,{'fixture':'verified'})
    calls=[]
    def run(self,command,**kwargs):
        assert not (f.root/receipt).exists(),'Receipt must be invalidated before package changes'
        assert os.fstat(self.lock_fd).st_uid==os.getuid()
        calls.append(command)
    monkeypatch.setattr(steps.ProcessIO,'run',run)
    if frozen:
        with pytest.raises(ValueError,match='required'):apply(f,[A])
        assert (f.root/receipt).exists() and calls==[]
    else:
        assert apply(f,[A])['state']=='completed'
        assert len(calls)==1 and calls[0][-3:]==('uninstall','--yes','sageattention')
        assert not (f.root/receipt).exists()


def test_shared_normalized_cache_retained_until_last_model_removed(components):
    from h3server.backends.catalog import get_artifact_set
    from h3server.backends.safetensors_compat import REPAIRS
    from pathlib import Path
    f=components;f.put(B)
    artifact=next(a for a in get_artifact_set(A).artifacts if a.sha256 in REPAIRS and any(b.sha256==a.sha256 for b in get_artifact_set(B).artifacts))
    path=f'cache/safetensors-compat/{artifact.sha256}/{Path(artifact.local_relative_path).name}'
    with regular_file(f.worker.paths,path,flags=os.O_CREAT|os.O_WRONLY,mode='wb',create_parents=True) as stream:stream.write(b'normalized fixture')
    assert apply(f,[B])['state']=='completed'
    assert (f.root/path).exists()
    assert apply(f,[])['state']=='completed'
    assert not (f.root/path).exists()
