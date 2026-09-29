"""Target-side adapter IO. No inference imports, networking or global side effects.

The remote worker owns process-group cancellation. Adapters emit domain Events,
write the daemon's selected output only, and never infer success from imports.
Filesystem helpers use POSIX no-follow directory descriptors on the Linux target;
these are not controller trust-file helpers (including on Windows).
"""
from contextlib import contextmanager
import json
import os
from pathlib import PurePosixPath
import re
import stat
import uuid

from h3server.domain import Event, StageStatus


def parts(relative):
    if not isinstance(relative, str) or not relative or any(
        not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", p) or p.endswith(".")
        for p in relative.split("/")
    ):
        raise ValueError("invalid confined path")
    return PurePosixPath(relative).parts


@contextmanager
def directory(paths, relative="", *, create=False):
    fd = os.open(paths.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts(relative) if relative else ():
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


@contextmanager
def regular_file(paths, relative, *, flags=os.O_RDONLY, mode="rb", create_parents=False):
    components = parts(relative)
    with directory(paths, "/".join(components[:-1]), create=create_parents) as parent:
        fd = os.open(components[-1], flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("expected unaliased regular file")
            stream = os.fdopen(fd, mode)
        except BaseException:
            os.close(fd)
            raise
        with stream:
            yield stream


def read_json(paths, relative, limit=65536):
    with regular_file(paths, relative) as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("JSON size limit")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs)


def atomic_json(paths, relative, data):
    components = parts(relative)
    with directory(paths, "/".join(components[:-1]), create=True) as parent:
        temporary = "." + uuid.uuid4().hex
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(data, stream, allow_nan=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, components[-1], src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


@contextmanager
def stage(name, emit):
    emit(Event(name, StageStatus.RUNNING))
    try:
        yield
    except BaseException as error:
        from h3server.installer.diagnostics import safe_log
        message = safe_log(f'{type(error).__name__}: {error}')
        emit(Event(name, StageStatus.FAILED, message[:4000]))
        raise
    else:
        emit(Event(name, StageStatus.COMPLETED))
