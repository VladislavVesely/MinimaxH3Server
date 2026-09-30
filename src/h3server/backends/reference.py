"""Separately consented Ref2VA INT8 weights; no FL2VA substitution or runtime downloads."""
import hashlib
import json
import os
import sys
from h3server.backends.base import read_json, regular_file
from h3server.backends.turbo import _verify

COMPONENT = 'reference'
REVISION = '908eccad7e68751190d04c171956f163bfeed741'
WEIGHT = 'MiniMax_H3_Ref2VA_pruned_int8_convrot.safetensors'
FILES = [dict(name=WEIGHT, size=20970379689, sha256='ed7e9aa541b945f44298650f0dc2e2e867bfd3b67cd07bcc90f6081c168f0d3d',
              url=f'https://huggingface.co/Abiray/Minimax-H3-nvfp4-INT4-INT8-Convrot/resolve/{REVISION}/{WEIGHT}')]
DIGEST = hashlib.sha256(json.dumps(FILES, sort_keys=True).encode()).hexdigest()
TOTAL = FILES[0]['size']
DIRECTORY = 'models/reference-' + DIGEST
CONSENT = 'config/reference-consent.json'
RECEIPT = 'state/reference-receipt.json'
SUPPORTED_PROFILES = ('h3-convrot-int8',)
BINDING = dict(artifact_digest=DIGEST, revision=REVISION, license='MiniMax H3 Community License', approved=True)

def require_consent(paths):
    if read_json(paths, CONSENT) != BINDING:
        raise ValueError('Explicit pinned Ref2VA download consent required')

def installed(paths, *, verify=False):
    try:
        require_consent(paths)
        if read_json(paths, RECEIPT) != BINDING:return False
        with regular_file(paths, DIRECTORY+'/'+WEIGHT) as stream:
            if verify:_verify(stream, FILES[0])
            elif os.fstat(stream.fileno()).st_size != TOTAL:return False
        return True
    except (OSError, ValueError):return False

def require(paths, digest=None):
    if digest is not None and digest != DIGEST:raise ValueError('Ref2VA selection changed')
    if not installed(paths, verify=True):raise ValueError('Install the pinned Ref2VA checkpoint before reference generation')

def runtime_directory(paths):
    from types import SimpleNamespace
    from h3server.backends.safetensors_compat import artifact_directory
    require(paths)
    artifact=SimpleNamespace(sha256=FILES[0]['sha256'],size_bytes=TOTAL,local_relative_path=WEIGHT)
    return artifact_directory(paths, paths.root/DIRECTORY, artifact)


def download(paths, *, progress, cancelled=lambda:False):
    from h3server.backends.turbo import download as transfer
    transfer(paths, progress=progress, cancelled=cancelled, _component=sys.modules[__name__])
