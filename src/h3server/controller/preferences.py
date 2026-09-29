"""Opt-in local plaintext preferences; passwords never appear in public views."""
import json
import os
from pathlib import Path
import stat
import threading
import uuid


class Preferences:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / 'preferences.json'
        self.lock = threading.RLock()

    def _prepare(self):
        if self.directory.is_symlink():
            raise ValueError('unsafe preferences directory')
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('unsafe preferences directory')
        if os.name == 'posix' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError('preferences directory must be owner-only')

    def _load(self):
        self._prepare()
        if self.path.is_symlink():
            raise ValueError('unsafe preferences file')
        try:
            fd = os.open(self.path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        except FileNotFoundError:
            return {'install_sage': False, 'use_sage': False, 'connection': None}
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_size > 32768
                    or (os.name == 'posix' and (info.st_uid != os.getuid() or info.st_mode & 0o077))):
                raise ValueError('unsafe preferences file')
            data = json.loads(stream.read(32769))
        if type(data) is not dict or set(data) != {'install_sage', 'use_sage', 'connection'}:
            raise ValueError('invalid preferences')
        if any(type(data[k]) is not bool for k in ('install_sage', 'use_sage')):
            raise ValueError('invalid preferences')
        c = data['connection']
        if c is not None:
            if (type(c) is not dict or set(c) != {'host', 'port', 'username', 'password'}
                    or type(c['port']) is not int or not 1 <= c['port'] <= 65535
                    or any(not isinstance(c[k], str) or not 0 < len(c[k]) <= 4096 for k in ('host', 'username', 'password'))):
                raise ValueError('invalid saved connection')
        return data

    def _save(self, data):
        payload = json.dumps(data, ensure_ascii=True).encode()
        if len(payload) > 32768:
            raise ValueError('preferences too large')
        temporary = self.path.with_name('preferences.' + uuid.uuid4().hex + '.tmp')
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def public(self):
        with self.lock:
            data = self._load()
            c = data['connection']
            return {**data, 'connection': ({k: c[k] for k in ('host', 'port', 'username')} | {'password_saved': True}) if c else None}

    def settings(self, install_sage, use_sage):
        with self.lock:
            data = self._load()
            data.update(install_sage=install_sage, use_sage=use_sage)
            self._save(data)
            return self.public()

    def remember(self, target, password):
        with self.lock:
            data = self._load()
            host, port, username = target
            data['connection'] = dict(host=host, port=port, username=username, password=password)
            self._save(data)

    def saved(self):
        with self.lock:
            return self._load()['connection']

    def forget(self):
        with self.lock:
            data = self._load()
            data['connection'] = None
            self._save(data)
            return self.public()
