import os
import fcntl
import threading
from h3server.backends.base import atomic_json, regular_file
from test_components import components,apply,A,B


def test_cancel_during_addition_keeps_old_models_and_releases_gate(components,monkeypatch):
    f=components;entered=threading.Event();release=threading.Event()
    def install(*args):
        entered.set();assert release.wait(5)
    monkeypatch.setattr(f.sync,'_install',install)
    inventory=f.sync.inventory()
    from h3server.backends import load_manifest
    f.sync.start([B],inventory['digest'],load_manifest()['sources']['license']['sha256'],True,True,True)
    assert entered.wait(5)
    assert f.worker.maintenance_status()['active']
    state=f.sync.cancel(f.sync.status()['operation']);assert state['busy']
    release.set();f.sync.thread.join(5)
    assert f.sync.status()['state']=='cancelled'
    assert (f.root/next(iter(f.files(A)))).exists()
    assert not f.worker.maintenance_status()['active']


def test_recovery_waits_for_inherited_guard_and_never_releases_other_owner(components):
    f=components;operation='b'*32
    f.worker.begin_maintenance(operation,policy='reject')
    atomic_json(f.worker.paths,'state/component-sync.json',{'state':'running','operation':operation})
    with regular_file(f.worker.paths,'state/component-sync.lock',flags=os.O_CREAT|os.O_RDWR,mode='r+b',create_parents=True) as guard:
        fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        f.sync.recover();assert f.worker.maintenance_status()['active']
    f.sync.recover();assert not f.worker.maintenance_status()['active']
    assert f.sync.status()['state']=='interrupted'
    f.worker.begin_maintenance('c'*32,policy='reject')
    atomic_json(f.worker.paths,'state/component-sync.json',{'state':'running','operation':operation})
    f.sync.recover();assert f.worker.maintenance_status()['operation']=='c'*32
    assert f.worker.maintenance_status()['active']


def test_inventory_is_bound_to_this_daemon_instance(components):
    f=components
    other=f.module.Components(f.worker,f.sync.library,f.sync.installers)
    assert f.sync.inventory()['digest']!=other.inventory()['digest']


def test_private_partial_download_cleanup_preserves_unrelated_tmp(components):
    f=components
    from h3server.backends.catalog import get_artifact_set
    selected=get_artifact_set(B);a=selected.artifacts[0]
    relative=f'tmp/downloads/{selected.digest}/{a.local_relative_path}.part'
    with regular_file(f.worker.paths,relative,flags=os.O_CREAT|os.O_WRONLY,mode='wb',create_parents=True) as stream:stream.write(b'partial')
    keep=f.root/'tmp/keep';keep.write_text('keep')
    state=apply(f,[A]);assert state['state']=='completed',state
    assert not (f.root/relative).exists()
    assert keep.read_text()=='keep'
