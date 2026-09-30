"""Checksum-pinned derived copies of known upstream safetensors packing defects.

Original approved downloads remain untouched. Only known unreferenced trailers
are removed; both the full source and normalized tensor file have exact hashes.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import uuid
from h3server.backends.base import directory, regular_file

REPAIRS = {
    'ed7e9aa541b945f44298650f0dc2e2e867bfd3b67cd07bcc90f6081c168f0d3d':
        (73, '9255f52b6677845ad238f20dfaafa94727053694127ab7f255c048f0f9365779'),
    'f07a54277ca85b59e5243a924fb5c54d79768f820fc4155c1b614b6f613e9dbf':
        (72, 'e889202c41dafb67b10d67b97f0d8541508036a6090af23425a5c2615d03c47a'),
    '88539d729fd7e9f9de765498428cd8e9152b8420317ec0e7542a362fffb7e0e1':
        (61, '7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522'),
    '83043bf3afccf4cab4d7e114f6e04607e9687bef16930a2f6088d721a893d3c4':
        (61, '8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48'),
}


def artifact_directory(paths, snapshot, artifact):
    if artifact.sha256 not in REPAIRS:
        return (snapshot / artifact.local_relative_path).parent
    trailer, expected = REPAIRS[artifact.sha256]
    from safetensors import safe_open
    relative = 'cache/safetensors-compat/' + artifact.sha256
    name = Path(artifact.local_relative_path).name
    with directory(paths, relative, create=True) as fd:
        target = paths.root / relative / name
        try:
            with regular_file(paths, relative + '/' + name) as cached:
                if (os.fstat(cached.fileno()).st_size != artifact.size_bytes - trailer
                        or hashlib.file_digest(cached, 'sha256').hexdigest() != expected):
                    raise ValueError('normalized model cache checksum mismatch')
            with safe_open(str(target), framework='pt', device='cpu'):
                pass
            return target.parent
        except FileNotFoundError:
            pass
        temporary = name + '.' + uuid.uuid4().hex
        try:
            source_relative = str((snapshot / artifact.local_relative_path).relative_to(paths.root))
            with regular_file(paths, source_relative) as source:
                header_size = struct.unpack('<Q', source.read(8))[0]
                if not 0 < header_size <= 64 * 1024 * 1024:
                    raise ValueError('invalid safetensors header size')
                header = json.loads(source.read(header_size))
                end = max(v['data_offsets'][1] for k, v in header.items() if k != '__metadata__') + header_size + 8
                if os.fstat(source.fileno()).st_size != artifact.size_bytes or artifact.size_bytes - end != trailer:
                    raise ValueError('upstream safetensors defect does not match known repair')
                source.seek(0)
                digest = hashlib.sha256()
                normalized = hashlib.sha256()
                position = 0
                output_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                with os.fdopen(output_fd, 'wb') as output:
                    while chunk := source.read(8 * 1024 * 1024):
                        digest.update(chunk)
                        retained = chunk[:max(0, min(len(chunk), end - position))]
                        normalized.update(retained)
                        output.write(retained)
                        position += len(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if (position != artifact.size_bytes or digest.hexdigest() != artifact.sha256
                        or normalized.hexdigest() != expected):
                    raise ValueError('model checksum mismatch; normalization refused')
            with safe_open(str(target.parent / temporary), framework='pt', device='cpu'):
                pass
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass
    return target.parent
