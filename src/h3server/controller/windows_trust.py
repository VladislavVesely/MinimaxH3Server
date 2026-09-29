"""Native Windows owner-only trust storage (pywin32; no chmod approximation).

Handles pin every ancestor against rename/deletion and reject reparse points.
Files are opened without sharing; owner and link count are checked on that same
handle before reading or changing its protected DACL. Other users, including
inherited groups, receive no ACE. Administrators can still take ownership, as on
POSIX. A filesystem without working Windows security descriptors fails closed.
"""
from contextlib import contextmanager, ExitStack
from pathlib import Path
import uuid


def _api():
    import win32api
    import win32con
    import win32file
    import win32security
    import pywintypes
    return win32api, win32con, win32file, win32security, pywintypes


def _identity():
    api, con, _, security, _ = _api()
    with security.OpenProcessToken(api.GetCurrentProcess(), con.TOKEN_QUERY) as token:
        return security.GetTokenInformation(token, security.TokenUser)[0]


def _descriptor(sid):
    _, _, file, security, types = _api()
    acl = security.ACL()
    # Every file is created with its own protected descriptor. Do not propagate
    # directory ACL changes into unvalidated existing child objects.
    acl.AddAccessAllowedAceEx(security.ACL_REVISION, 0, file.FILE_ALL_ACCESS, sid)
    sd = security.SECURITY_DESCRIPTOR()
    sd.SetSecurityDescriptorOwner(sid, False)
    sd.SetSecurityDescriptorDacl(True, acl, False)
    sd.SetSecurityDescriptorControl(security.SE_DACL_PROTECTED, security.SE_DACL_PROTECTED)
    attrs = types.SECURITY_ATTRIBUTES()
    attrs.SECURITY_DESCRIPTOR = sd
    return acl, attrs


def _check(handle, *, directory, sid=None):
    _, con, file, security, _ = _api()
    info = file.GetFileInformationByHandle(handle)
    if info[0] & con.FILE_ATTRIBUTE_REPARSE_POINT or bool(info[0] & con.FILE_ATTRIBUTE_DIRECTORY) != directory:
        raise OSError('unsafe trust path')
    if not directory and info[7] != 1:
        raise OSError('hardlinked trust file')
    if sid is not None:
        sd = security.GetSecurityInfo(handle, security.SE_FILE_OBJECT, security.OWNER_SECURITY_INFORMATION)
        if sd.GetSecurityDescriptorOwner() != sid:
            raise OSError('foreign trust owner')
        acl, _ = _descriptor(sid)
        security.SetSecurityInfo(handle, security.SE_FILE_OBJECT,
            security.DACL_SECURITY_INFORMATION | security.PROTECTED_DACL_SECURITY_INFORMATION,
            None, None, acl, None)
        sd = security.GetSecurityInfo(handle, security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION)
        actual = sd.GetSecurityDescriptorDacl()
        if (not sd.GetSecurityDescriptorControl()[0] & security.SE_DACL_PROTECTED
                or actual is None or actual.GetAceCount() != 1 or actual.GetAce(0) != acl.GetAce(0)):
            raise OSError('private ACL unavailable')


@contextmanager
def directory_lock(directory):
    _, con, file, _, types = _api()
    directory = Path(directory).absolute()
    sid = _identity()
    with ExitStack() as stack:
        # Never follow junctions/symlinks, including above the selected directory.
        for path in [*reversed(directory.parents), directory]:
            own = path == directory
            if not path.exists():
                _, attrs = _descriptor(sid)
                try:
                    file.CreateDirectory(str(path), attrs)
                except types.error as exc:
                    if exc.winerror != 183:  # A concurrent creator must pass the checks below.
                        raise
            # SetSecurityInfo explicitly does not propagate to children when
            # the directory handle was opened with MAXIMUM_ALLOWED.
            access = con.MAXIMUM_ALLOWED if own else con.READ_CONTROL
            handle = file.CreateFile(str(path), access,
                con.FILE_SHARE_READ | con.FILE_SHARE_WRITE, None, con.OPEN_EXISTING,
                con.FILE_FLAG_BACKUP_SEMANTICS | file.FILE_FLAG_OPEN_REPARSE_POINT, None)
            stack.callback(handle.Close)
            _check(handle, directory=True, sid=sid if own else None)
        yield sid


def read(directory):
    _, con, file, _, types = _api()
    with directory_lock(directory) as sid:
        try:
            handle = file.CreateFile(str(Path(directory) / 'known_hosts'),
                con.GENERIC_READ | con.READ_CONTROL | con.WRITE_DAC, 0, None,
                con.OPEN_EXISTING, file.FILE_FLAG_OPEN_REPARSE_POINT, None)
        except types.error as exc:
            if exc.winerror == 2:
                return ''
            raise
        with handle:
            _check(handle, directory=False, sid=sid)
            size = file.GetFileSize(handle)
            if size > 1024 * 1024:
                raise OSError('trust file too large')
            return file.ReadFile(handle, size)[1].decode('utf-8') if size else ''


def write(directory, text):
    _, con, file, _, _ = _api()
    with directory_lock(directory) as sid:
        _, attrs = _descriptor(sid)
        temporary = Path(directory) / ('.known_hosts-' + uuid.uuid4().hex)
        try:
            with file.CreateFile(str(temporary), con.GENERIC_WRITE | con.READ_CONTROL | con.WRITE_DAC,
                    0, attrs, con.CREATE_NEW, file.FILE_FLAG_OPEN_REPARSE_POINT, None) as handle:
                _check(handle, directory=False, sid=sid)
                file.WriteFile(handle, text.encode('utf-8'))
                file.FlushFileBuffers(handle)
            file.MoveFileEx(str(temporary), str(Path(directory) / 'known_hosts'),
                            con.MOVEFILE_REPLACE_EXISTING | file.MOVEFILE_WRITE_THROUGH)
        finally:
            temporary.unlink(missing_ok=True)
