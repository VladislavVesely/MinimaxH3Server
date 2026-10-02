"""Owner-only, stable controller access; reset is explicit, not a shutdown side effect."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time
import uuid

from .preferences import Preferences


class LocalAccess:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.preferences = Preferences(directory)
        self.preferences.path = self.directory / 'local-access.json'

    @staticmethod
    def _check(info):
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 4096
                or (os.name == 'posix' and (info.st_uid != os.getuid() or info.st_mode & 0o077))):
            raise ValueError('unsafe local access file')

    @contextmanager
    def _locked(self):
        self.preferences._prepare()
        if os.name == 'nt':
            from . import windows_trust as win
            with win.directory_lock(self.directory) as sid:
                _, con, file, _, types = win._api()
                _, attrs = win._descriptor(sid)
                deadline = time.monotonic() + 10
                while True:
                    try:
                        handle = file.CreateFile(str(self.directory / '.local-access.lock'),
                            con.GENERIC_READ | con.GENERIC_WRITE | con.READ_CONTROL | con.WRITE_DAC,
                            0, attrs, con.OPEN_ALWAYS, file.FILE_FLAG_OPEN_REPARSE_POINT, None)
                        break
                    except types.error as error:
                        if error.winerror != 32 or time.monotonic() >= deadline:
                            raise
                        time.sleep(.01)
                with handle:
                    win._check(handle, directory=False, sid=sid)
                    yield
        else:
            import fcntl
            fd = os.open(self.directory / '.local-access.lock',
                         os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            with os.fdopen(fd, 'rb') as stream:
                self._check(os.fstat(stream.fileno()))
                fcntl.flock(stream, fcntl.LOCK_EX)
                yield

    def _read(self, name):
        if os.name == 'nt':
            from . import windows_trust as win
            _, con, file, _, types = win._api()
            try:
                handle = file.CreateFile(str(self.directory / name),
                    con.GENERIC_READ | con.READ_CONTROL | con.WRITE_DAC, 0, None,
                    con.OPEN_EXISTING, file.FILE_FLAG_OPEN_REPARSE_POINT, None)
            except types.error as error:
                if error.winerror == 2:
                    return None
                raise
            with handle:
                win._check(handle, directory=False, sid=win._identity())
                size = file.GetFileSize(handle)
                if size > 4096:
                    raise ValueError('local access file too large')
                return file.ReadFile(handle, size)[1].decode('utf-8') if size else ''
        try:
            fd = os.open(self.directory / name, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
        except FileNotFoundError:
            return None
        with os.fdopen(fd, 'rb') as stream:
            self._check(os.fstat(stream.fileno()))
            return stream.read(4097).decode('utf-8')

    def _write(self, name, text):
        temporary = self.directory / ('.local-access-' + uuid.uuid4().hex)
        try:
            if os.name == 'nt':
                from . import windows_trust as win
                _, con, file, _, _ = win._api()
                _, attrs = win._descriptor(win._identity())
                with file.CreateFile(str(temporary), con.GENERIC_WRITE | con.READ_CONTROL | con.WRITE_DAC,
                        0, attrs, con.CREATE_NEW, file.FILE_FLAG_OPEN_REPARSE_POINT, None) as handle:
                    win._check(handle, directory=False, sid=win._identity())
                    file.WriteFile(handle, text.encode('utf-8'))
                    file.FlushFileBuffers(handle)
                file.MoveFileEx(str(temporary), str(self.directory / name),
                                con.MOVEFILE_REPLACE_EXISTING | file.MOVEFILE_WRITE_THROUGH)
            else:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(text.encode('utf-8'))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.directory / name)
        finally:
            temporary.unlink(missing_ok=True)

    def _record(self):
        text = self._read('local-access.json')
        if text is None:
            return None
        record = json.loads(text)
        self._validate(record)
        return record

    @staticmethod
    def _validate(record):
        if (type(record) is not dict or set(record) != {'schema', 'key', 'cookie', 'csrf', 'remember'}
                or type(record['schema']) is not int or record['schema'] != 1
                or any(not isinstance(record[name], str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', record[name])
                       for name in ('key', 'cookie', 'csrf', 'remember'))):
            raise ValueError('invalid local access state')

    def _new(self, key=None, cookie=None):
        record = dict(schema=1, key=key or secrets.token_urlsafe(32),
                      cookie=cookie or secrets.token_urlsafe(32), csrf=secrets.token_urlsafe(32),
                      remember=secrets.token_urlsafe(32))
        self._validate(record)
        self._write('local-access.json', json.dumps(record))
        return record

    def initialize(self, *, key=None, cookie=None):
        with self._locked():
            record = self._record()
            # An explicitly supplied embedding key is an intentional configuration change.
            if record is None or (key is not None and record['key'] != key):
                record = self._new(key, cookie)
            return record

    def reset(self):
        with self._locked():
            self._record()  # Do not silently overwrite unsafe/corrupt existing credentials.
            exported = self._read('lan-access.txt') is not None
            record = self._new()
            if exported:
                self._write_export(record)
            return record

    def _write_export(self, record):
        self._write('lan-access.txt', 'Access key: ' + record['key'] + '\n\n'
                    'Trusted LAN only: HTTP is unencrypted. This key survives restarts.\n'
                    'Use --reset-access-key to revoke remembered browsers and replace the key.\n')

    def export(self):
        with self._locked():
            record = self._record() or self._new()
            self._read('lan-access.txt')  # Validate an existing export before replacement.
            self._write_export(record)
        return self.directory / 'lan-access.txt'
