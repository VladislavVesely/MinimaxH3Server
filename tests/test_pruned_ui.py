"""Controller presentation/approval contracts; no target IO or model weights."""
import pytest

from h3server.backends.catalog import PRUNED_PROFILE_NAMES, get_artifact_set
from h3server.controller.deploy import Deployment
from h3server.installer.planner import build_plan
from test_controller_security import API, bootstrap, controller  # noqa: F401
from test_deploy import bundle
from test_install_plan import facts


def test_catalog_is_authenticated_and_exact(controller):
    app, client, network = controller
    assert client.get(API + '/profiles').status_code == 401
    bootstrap(app, client)
    data = client.get(API + '/profiles').json()
    assert data['profiles'][0]['profile_name'] == 'h3-gguf-q4-k-m'
    for name in PRUNED_PROFILE_NAMES:
        document = next(p for p in data['profiles'] if p['profile_name'] == name)
        selected = get_artifact_set(name)
        assert document['artifact_set_digest'] == selected.digest
        assert document['weight_bytes'] == selected.weight_bytes
        assert document['selected_bytes'] == selected.selected_bytes
        assert sum(a['size_bytes'] for a in document['artifacts']) == selected.weight_bytes
        assert [a['url'] for a in document['artifacts']] == [a.url for a in selected.artifacts]
        assert document['memory_status'] == 'unmeasured'
        assert document['runtime_bytes'] is None
        assert document['license_disclosures'] == list(selected.license_disclosures)
    assert data['profiles'][-1]['runtime_id'] == 'diffusers-minimax-h3-t2va'
    assert data['unavailable'][0]['profile_name'] == 'h3-convrot-int4'
    assert network.sockets == []


@pytest.mark.parametrize('name', PRUNED_PROFILE_NAMES)
def test_plan_document_binds_visible_download_to_selected_profile(tmp_path, name):
    deploy = Deployment(None, bundle(tmp_path))
    deploy.plan = build_plan(facts(), profile=name, artifact=deploy.release.artifact)
    document = deploy.document()
    assert document['selection']['profile_name'] == name
    assert document['selection']['artifact_set_digest'] == document['plan']['artifact_set_digest']
    assert document['selection']['weight_bytes'] != 144051160615
    assert 'MiniMax' in document['license_text']


def test_download_action_forwards_exact_artifact_digest(controller, monkeypatch):
    app, client, _ = controller
    headers = bootstrap(app, client)
    received = []
    monkeypatch.setattr(app.state.deployment, 'action', lambda action, body: received.append((action, body)) or {'state': 'accepted'})
    selected = get_artifact_set('h3-convrot-mixed')
    body = dict(plan_digest='a'*64, artifact_set_digest=selected.digest,
                license_sha256='b'*64, license_acknowledged=True,
                eligibility_confirmed=True, download_approved=True)
    assert client.post(API + '/setup/download', json=body, headers=headers).status_code == 200
    assert received[0][1]['artifact_set_digest'] == selected.digest
    body['artifact_set_digest'] = 'invalid'
    assert client.post(API + '/setup/download', json=body, headers=headers).status_code == 422
    assert len(received) == 1


@pytest.mark.parametrize('gpu,eligible', [('NVIDIA GeForce RTX 4090', False), ('NVIDIA GeForce RTX 5090', True), ('Unknown GPU', None)])
def test_scan_exposes_smaller_default_and_positive_blackwell_gate(controller, gpu, eligible):
    from test_controller_security import connect
    app, client, network = controller
    headers = bootstrap(app, client)
    fingerprint = connect(client, headers).json()['fingerprint']
    client.post(API + '/approve', json={'fingerprint': fingerprint}, headers=headers)
    client.post(API + '/authenticate', json={'password': 'TEST ONLY'}, headers=headers)
    network.outputs.update({
        'LC_ALL=C cat /proc/meminfo': b'MemTotal: 30408704 kB\n',
        'LC_ALL=C nvidia-smi --query-gpu=pci.bus_id,name,memory.total,driver_version --format=csv,noheader,nounits':
            f'00000000:01:00.0, {gpu}, 24564, 580.1\n'.encode()})
    data = client.post(API + '/scan', json={}, headers=headers).json()
    assert data['profiles'][0]['profile_name'] == 'h3-gguf-q4-k-m'
    assert next(p for p in data['profiles'] if p['profile_name'] == 'h3-nvfp4')['gpu_eligible'] is eligible
    q4 = data['recommendations'][0]
    assert q4['profile_name'] == 'h3-gguf-q4-k-m'
    assert not any('RAM bytes:' in reason and 'below required' in reason for reason in q4['reasons'])
