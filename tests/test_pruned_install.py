"""Installer process contracts only: no pip/source download or OS changes."""
from copy import deepcopy
import pytest
from h3server.backends.catalog import get_artifact_set, PRUNED_PROFILE_NAMES
from h3server.installer.planner import build_plan, validate_plan, ReleaseArtifact
from test_install_plan import facts


def prepared_pruned(tmp_path):
    from test_installer import prepared
    paths, legacy, store = prepared(tmp_path)
    plan = build_plan(facts(), profile=PRUNED_PROFILE_NAMES[0], artifact=ReleaseArtifact(**legacy['artifact']))
    return paths, plan, store


@pytest.mark.parametrize('profile', PRUNED_PROFILE_NAMES)
def test_pruned_plan_binds_runtime_not_legacy(profile):
    p = build_plan(facts(), profile=profile, artifact=ReleaseArtifact('v1','h3server-0.1.0-py3-none-any.whl','a'*64))
    a = get_artifact_set(profile)
    assert p['runtime']['runtime_id'] == a.runtime_id
    assert p['artifact_set_digest'] == a.digest
    assert p['runtime']['sources'][0]['revision'] == a.runtime_sources[0].revision
    assert not any('diffusers' in r or 'torchao' in r for r in p['requirements'])
    assert p['runtime']['weight_download_approved'] is False
    assert p['model_revision'] == a.artifacts[0].revision
    validate_plan(p)
    for key in ['artifact_set_digest','runtime','requirements','backend_revision','model_revision']:
        changed = deepcopy(p); changed[key] = 'tampered'
        with pytest.raises(ValueError): validate_plan(changed)


def test_pruned_replan_stays_pruned():
    p = build_plan(facts(python_ready=False), profile=PRUNED_PROFILE_NAMES[0], artifact=ReleaseArtifact('v1','h3server-0.1.0-py3-none-any.whl','a'*64))
    assert p['purpose'] == 'prerequisites'
    assert p['stages'] == ['system-packages','replan']
    assert p['artifact_set_digest'] == get_artifact_set(PRUNED_PROFILE_NAMES[0]).digest


def test_installer_provisions_only_approved_runtime_and_records_freeze(tmp_path,monkeypatch):
    from h3server.backends import comfy_runtime
    from h3server.installer.runner import Installer
    from h3server.store import SetupState
    from test_installer import FakeIO
    from test_install_plan import approval
    import json
    paths,p,store = prepared_pruned(tmp_path)
    verified=[]
    monkeypatch.setattr(comfy_runtime,'verify_sources',lambda *a: verified.append(a))
    io=FakeIO(); runner=Installer(paths,store,io=io)
    setup=runner.create(p); done=runner.run(setup.setup_id,approval(p))
    assert done.state == SetupState.READY
    env=paths.environments/done.plan_digest
    assert verified == [(env,p['profile'])]
    calls=[c for c,e in io.calls]
    fetches=[c for c in calls if 'fetch' in c]
    assert [c[-2:] for c in fetches] == [('https://github.com/'+s['repository']+'.git',s['revision']) for s in p['runtime']['sources']]
    pip=next(c for c in calls if 'install' in c and 'pip' in c)
    assert '--only-binary=:all:' in pip and not any('diffusers' in v for v in pip)
    assert str(env/'ComfyUI/requirements.txt') in pip
    assert str(env/'ComfyUI-GGUF/requirements.txt') in pip
    smoke=next(c for c in calls if '--environment-only' in c)
    assert smoke[-2:] == ('--profile',p['profile'])
    assert 'h3server.backends.pruned' in smoke
    record=json.loads((env/'runtime.json').read_text())
    assert record == comfy_runtime.runtime_record(p['profile'],(env/'resolved.txt').read_text())
    assert json.loads((env/'readiness.json').read_text()) == dict(environment_ready=True,model_ready=False,generation_tested=False)
    assert not any('h3server.downloads' in c for c in calls)


def test_source_verification_failure_prevents_pip_install(tmp_path,monkeypatch):
    from h3server.backends import comfy_runtime
    from h3server.installer.runner import Installer
    from test_installer import FakeIO
    from test_install_plan import approval
    paths,p,store=prepared_pruned(tmp_path)
    def fail(*a): raise ValueError('source integrity')
    monkeypatch.setattr(comfy_runtime,'verify_sources',fail)
    io=FakeIO(); runner=Installer(paths,store,io=io); setup=runner.create(p)
    with pytest.raises(ValueError,match='integrity'): runner.run(setup.setup_id,approval(p))
    assert not any('install' in c and 'pip' in c for c,e in io.calls)
    assert not list(paths.environments.glob('*/readiness.json'))


def test_pruned_service_units_bind_profile_before_activation(tmp_path):
    from h3server.installer.recovery import render_units,service_plan
    from h3server.store import canonical_plan_digest
    paths,p,_=prepared_pruned(tmp_path)
    unit=render_units(paths,p)['h3server.service']
    assert '--profile "h3-gguf-q4-k-m"' in unit
    assert str(paths.environments/canonical_plan_digest(p)/'bin/python') in unit
    digest=canonical_plan_digest(service_plan(paths,p,uid=1000))
    changed=build_plan(facts(),profile='h3-convrot-mixed',artifact=ReleaseArtifact(**p['artifact']))
    assert canonical_plan_digest(service_plan(paths,changed,uid=1000)) != digest
    assert '--profile "h3-convrot-mixed"' in render_units(paths,changed)['h3server.service']


def test_unknown_cli_profile_rejected_and_pruned_selection_forwarded():
    from h3server.remote.__main__ import configuration
    root,port,config=configuration(['--root','/tmp/example','--profile','h3-convrot-mixed'])
    assert config == {'profile':'h3-convrot-mixed'}
    with pytest.raises(ValueError): configuration(['--profile','missing'])


def test_activation_records_pruned_runtime_and_rolls_back_unit(tmp_path):
    from h3server.controller.target import Activation,active_release
    from h3server.backends.comfy_runtime import runtime_record
    from h3server.backends.base import atomic_json,read_json
    from h3server.store import canonical_plan_digest
    from test_deploy import RuntimeIO
    import json
    paths,p,_=prepared_pruned(tmp_path)
    digest=canonical_plan_digest(p); env=paths.environments/digest; env.mkdir()
    (env/'resolved.txt').write_text('controlled==1\n')
    atomic_json(paths,f'environments/{digest}/readiness.json',{'environment_ready':True})
    atomic_json(paths,f'environments/{digest}/runtime.json',runtime_record(p['profile'],'controlled==1\n'))
    io=RuntimeIO(paths,previous=False)
    result=Activation(paths,io=io).run(p,'a'*32,policy='reject')
    assert result == dict(daemon_ready=True,installation_ready=True,model_ready=False,generation_tested=False)
    active=active_release(paths)
    assert '--profile "h3-gguf-q4-k-m"' in active['unit']
    record=read_json(paths,f"releases/{p['artifact']['release_id']}/runtime-{digest}.json")
    assert record['artifact_set_digest'] == p['artifact_set_digest'] and record['runtime'] == p['runtime']
    journal=read_json(paths,'state/activation.json')
    # Controlled interrupted first-activation rollback revokes autostart and
    # pointer; no service/hardware operations are performed by this fixture.
    journal['phase']='switching'
    Activation(paths,io=io).rollback(journal)
    assert active_release(paths) is None and not paths.current.exists()
    assert ('service','disable','h3server.service') in io.calls


def test_activation_rejects_mismatched_runtime_before_service_io(tmp_path):
    from h3server.controller.target import Activation
    from h3server.backends.base import atomic_json
    from h3server.store import canonical_plan_digest
    from test_deploy import RuntimeIO
    paths,p,_=prepared_pruned(tmp_path)
    digest=canonical_plan_digest(p); env=paths.environments/digest; env.mkdir()
    (env/'resolved.txt').write_text('controlled==1\n')
    atomic_json(paths,f'environments/{digest}/readiness.json',{'environment_ready':True})
    atomic_json(paths,f'environments/{digest}/runtime.json',{'profile':'wrong'})
    io=RuntimeIO(paths,previous=False)
    with pytest.raises(ValueError,match='approved profile'):
        Activation(paths,io=io).run(p,'a'*32,policy='reject')
    assert not io.calls
