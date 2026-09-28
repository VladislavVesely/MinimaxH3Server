"""DESTRUCTIVE, explicitly opt-in disposable Linux VM acceptance; skipped normally.

Never use server.txt or a production server. Provision a disposable VM with a
pinned known_hosts entry, staged release/approved setup, completed prepare(), and
a WAITING_FOR_REBOOT checkpoint. The separately approved staged approval.json
must have reboot=true. This test downloads no weights, but the approved bootstrap
may finish its previously approved environment installation after boot.

Required environment:
 H3_VM_REBOOT_CONSENT=I_ACCEPT_REBOOT_OF_DISPOSABLE_VM
 H3_DISPOSABLE_VM_CONFIG=/absolute/path/to/private.json
 H3_VM_SSH_PASSWORD=<in-memory CI secret>; optional H3_VM_SUDO_PASSWORD

Config: {"disposable": true, "host": ..., "port": 22, "username": ...,
 "known_hosts_directory": ..., "root": ..., "plan_digest": ..., "marker": <UUID>,
 "artifact": {"release_id": ..., "filename": ..., "sha256": ...}}
The VM administrator must create /etc/h3server-disposable-vm containing precisely
that UUID; it must be owned by root and not group/other-writable. Both local
explicit consent and the remote marker are checked before invoking reboot.
Native VM results must be reported separately from fixture test results.
"""
import json
import os
from pathlib import Path
import time
from uuid import UUID

import pytest

from h3server.controller.ssh import SSHConnection
from h3server.installer.planner import ReleaseArtifact
from h3server.installer.recovery import reconnect


def test_disposable_vm_bootstrap_continues_after_real_reboot():
    if os.environ.get('H3_VM_REBOOT_CONSENT') != 'I_ACCEPT_REBOOT_OF_DISPOSABLE_VM':
        pytest.skip('real reboot requires explicit disposable-VM consent; simulation is not VM validation')
    config_path = os.environ.get('H3_DISPOSABLE_VM_CONFIG')
    assert config_path and Path(config_path).is_absolute(), 'explicit disposable target config required'
    config = json.loads(Path(config_path).read_text())
    assert config['disposable'] is True
    marker = str(UUID(config['marker']))
    password = os.environ['H3_VM_SSH_PASSWORD']
    target = (config['host'], config['port'], config['username'])
    factory = lambda: SSHConnection(Path(config['known_hosts_directory']))
    connection = reconnect(factory, target, password, timeout=30)
    artifact = ReleaseArtifact(**config['artifact'])
    request = dict(root=config['root'], artifact=artifact, digest=config['plan_digest'])

    def remote_read(path):
        client = connection.open_sftp()
        try:
            with client.open(path, 'rb') as stream:
                return stream.read(32768).decode().strip()
        finally:
            client.close()

    try:
        client = connection.open_sftp()
        try:
            info = client.lstat('/etc/h3server-disposable-vm')
            assert info.st_uid == 0 and info.st_mode & 0o022 == 0
            import stat
            assert stat.S_ISREG(info.st_mode)
        finally:
            client.close()
        assert remote_read('/etc/h3server-disposable-vm') == marker
        before_boot = str(UUID(remote_read('/proc/sys/kernel/random/boot_id')))
        before_result = connection.recovery(**request, action='snapshot')
        assert before_result.succeeded
        before = json.loads(before_result.stdout)
        setup = next(s for s in before['setups'] if s['plan_digest'] == config['plan_digest'])
        assert setup['state'] == 'waiting_for_reboot'
        assert setup['stages'][0]['status'] == 'completed'
        result = connection.recovery(**request, action='reboot', sudo_password=os.environ.get('H3_VM_SUDO_PASSWORD'))
        assert result.succeeded, 'only detached request acceptance, not completed reboot'
        connection.close()
        deadline = time.monotonic() + 600
        changed = False
        while time.monotonic() < deadline:
            time.sleep(2)
            connection = reconnect(factory, target, password, timeout=min(60, max(.01, deadline - time.monotonic())))
            try:
                changed = str(UUID(remote_read('/proc/sys/kernel/random/boot_id'))) != before_boot
                snapshot = connection.recovery(**request, action='snapshot', after=before['cursor'])
                assert snapshot.succeeded
                current = json.loads(snapshot.stdout)
                resumed = next(s for s in current['setups'] if s['setup_id'] == setup['setup_id'])
                assert resumed['state'] != 'failed', 'native continuation failed; not environment ready'
                if changed and resumed['state'] == 'ready':
                    assert len([s for s in current['setups'] if s['plan_digest'] == config['plan_digest']]) == 1
                    assert current['events'], 'durable post-reboot events must replay'
                    break
            finally:
                connection.close()
        else:
            pytest.fail(f'native reboot/continuation deadline expired (boot changed={changed})')
    finally:
        connection.close()
