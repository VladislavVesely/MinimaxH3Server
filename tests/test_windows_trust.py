"""Native Windows acceptance cases; explicitly skipped elsewhere, not certification."""
import os
import subprocess

import paramiko
import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='native Windows ACL/reparse APIs required')


def assert_private(path):
    import win32security as security
    from h3server.controller.windows_trust import _identity
    sd = security.GetFileSecurity(str(path), security.OWNER_SECURITY_INFORMATION | security.DACL_SECURITY_INFORMATION)
    assert sd.GetSecurityDescriptorOwner() == _identity()
    assert sd.GetSecurityDescriptorControl()[0] & security.SE_DACL_PROTECTED
    acl = sd.GetSecurityDescriptorDacl()
    assert acl.GetAceCount() == 1
    assert acl.GetAce(0)[0][0] == security.ACCESS_ALLOWED_ACE_TYPE
    assert acl.GetAce(0)[2] == _identity()


def test_windows_owner_acl_and_fingerprint_persistence(tmp_path):
    from h3server.controller.ssh import KnownHosts, ControllerError
    store = KnownHosts(tmp_path / 'state')
    key = paramiko.RSAKey.generate(1024)
    store.approve('[example.org]:22', key)
    assert store.load().check('[example.org]:22', key)
    assert_private(store.directory)
    assert_private(store.path)
    with pytest.raises(ControllerError, match='host_key_changed'):
        store.approve('[example.org]:22', paramiko.RSAKey.generate(1024))


def test_windows_hardlink_is_rejected(tmp_path):
    from h3server.controller.ssh import KnownHosts, ControllerError
    store = KnownHosts(tmp_path / 'state')
    store.load()
    target = tmp_path / 'unmodified'
    target.write_text('unmodified')
    os.link(target, store.path)
    with pytest.raises(ControllerError, match='state_unavailable'):
        store.load()
    assert target.read_text() == 'unmodified'


@pytest.mark.parametrize('ancestor', [False, True])
def test_windows_junction_is_rejected_without_touching_target(tmp_path, ancestor):
    from h3server.controller.ssh import KnownHosts, ControllerError
    target = tmp_path / 'target'
    target.mkdir()
    junction = tmp_path / 'junction'
    subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(target)], check=True, capture_output=True)
    try:
        with pytest.raises(ControllerError, match='state_unavailable'):
            KnownHosts(junction / 'state' if ancestor else junction).load()
        assert list(target.iterdir()) == []
    finally:
        junction.rmdir()


def test_windows_duplicate_records_rejected(tmp_path):
    from h3server.controller.ssh import KnownHosts, ControllerError
    from h3server.controller import windows_trust
    store = KnownHosts(tmp_path / 'state')
    key = paramiko.RSAKey.generate(1024)
    store.approve('[example.org]:22', key)
    text = windows_trust.read(store.directory)
    windows_trust.write(store.directory, text + text)
    with pytest.raises(ControllerError, match='state_unavailable'):
        store.load()


def test_windows_foreign_owner_fails_before_acl_change(tmp_path, monkeypatch):
    import win32security as security
    from h3server.controller.ssh import KnownHosts, ControllerError
    from h3server.controller import windows_trust
    store = KnownHosts(tmp_path / 'state')
    store.load()
    before = security.GetFileSecurity(str(store.directory), security.DACL_SECURITY_INFORMATION).GetSecurityDescriptorDacl().GetAce(0)
    monkeypatch.setattr(windows_trust, '_identity', lambda: security.CreateWellKnownSid(security.WinLocalSystemSid, None))
    with pytest.raises(ControllerError, match='state_unavailable'):
        store.load()
    after = security.GetFileSecurity(str(store.directory), security.DACL_SECURITY_INFORMATION).GetSecurityDescriptorDacl().GetAce(0)
    assert before == after


def test_windows_hardlink_rejection_does_not_change_target_acl(tmp_path):
    import win32security as security
    from h3server.controller.ssh import KnownHosts, ControllerError
    target = tmp_path / 'untouched'
    target.write_text('untouched')
    before = security.GetFileSecurity(str(target), security.DACL_SECURITY_INFORMATION).GetSecurityDescriptorDacl()
    before_aces = [before.GetAce(i) for i in range(before.GetAceCount())]
    store = KnownHosts(tmp_path / 'state')
    store.directory.mkdir()
    os.link(target, store.path)
    with pytest.raises(ControllerError, match='state_unavailable'):
        store.load()
    after = security.GetFileSecurity(str(target), security.DACL_SECURITY_INFORMATION).GetSecurityDescriptorDacl()
    assert [after.GetAce(i) for i in range(after.GetAceCount())] == before_aces
