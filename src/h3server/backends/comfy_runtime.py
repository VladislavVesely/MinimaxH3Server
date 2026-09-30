"""Pinned source/dependency provisioning contract; no inference imports or IO at import.

Fingerprints cover ALL tracked bytes at the source pins (including licenses and
bundled tokenizer), canonical sorted path -> SHA256 JSON. Runtime rejects extra
files too, except Git internals. Bytecode writing is disabled before imports.
Candidate wheels are not a measured GPU matrix. Each approved environment records
its target-resolved freeze/report and must pass native import/CUDA/codec smoke.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import os
import subprocess
import sys

from h3server.backends.catalog import get_artifact_set
from h3server.backends.base import read_json, regular_file, directory

SOURCE_TREES = {
    'Comfy-Org/ComfyUI': ('ComfyUI', '4595a17750229415aa8f067bf8036eecb8e82fcddfd253adfcebc2d543b60cae', 48322430),
    'city96/ComfyUI-GGUF': ('ComfyUI-GGUF', '0224788b3db97592c9151c1a35b6aaf76fee70947a23cc478d558fa843bf7428', 120270),
}
# Torch/torchvision pairing and PyPI Linux CUDA13 dependencies are recorded in the
# legacy metadata investigation, not inherited legacy model/offload requirements.
CANDIDATE_REQUIREMENTS = ['torch==2.14.0', 'torchvision==0.29.0', 'transformers==5.17.0',
                          'av==18.1.0', 'huggingface-hub>=1.32.0,<2.0']


def provision_contract(profile):
    selected = get_artifact_set(profile)
    sources = [dict(**asdict(s), directory=SOURCE_TREES[s.repository][0],
                    tree_sha256=SOURCE_TREES[s.repository][1], source_bytes=SOURCE_TREES[s.repository][2])
               for s in selected.runtime_sources]
    return dict(runtime_id=selected.runtime_id, sources=sources,
                dependency_status='target-resolved candidate; not inference-certified',
                weight_download_approved=False, weight_bytes=selected.weight_bytes,
                tokenizer_bytes=selected.tokenizer_bytes,
                # Includes tokenizer already; Git objects, wheels, unpacked packages
                # and upgrade coexistence remain additional, unresolved disk use.
                source_bytes=sum(s['source_bytes'] for s in sources),
                runtime_install_bytes=None, output_reserve_bytes=5 * 1024**3,
                license_disclosures=list(selected.license_disclosures))


def source_fingerprint(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('runtime source must be a real directory')
    entries = {}
    for parent, dirs, files in os.walk(root, followlinks=False):
        if Path(parent) == root:
            dirs[:] = [d for d in dirs if d != '.git']
        for name in dirs:
            if (Path(parent)/name).is_symlink():
                raise ValueError('linked runtime source directory')
        for name in files:
            path = Path(parent)/name
            if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
                raise ValueError('invalid runtime source file')
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                entries[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verify_sources(environment, profile):
    selected = get_artifact_set(profile)
    for source in provision_contract(profile)['sources']:
        path = Path(environment)/source['directory']
        if source_fingerprint(path) != source['tree_sha256']:
            raise ValueError('pinned runtime source integrity failed')
    for artifact in selected.tokenizers:
        path = Path(environment)/'ComfyUI'/artifact.path
        data = path.read_bytes()
        if len(data) != artifact.size_bytes or hashlib.sha256(data).hexdigest() != artifact.sha256:
            raise ValueError('bundled tokenizer integrity failed')


def runtime_record(profile, resolved):
    selected = get_artifact_set(profile)
    return dict(profile=profile, artifact_set_digest=selected.digest,
                runtime=provision_contract(profile), resolved_sha256=hashlib.sha256(resolved.encode()).hexdigest())


def dependencies_match(paths, resolved, current, *, allow_unverified_sage=False):
    """Permit only the exact additive component, never relax the base freeze.

    The installer alone can repair an interrupted pinned installation before its
    GPU receipt exists. Normal runtime/readiness always requires that receipt.
    """
    if current == resolved:
        return True
    from h3server.backends.sage_attention import VERSION, installed
    addition = f'sageattention=={VERSION}\n'
    lines = current.splitlines(keepends=True)
    if addition in resolved.splitlines(keepends=True) or lines.count(addition) != 1:
        return False
    if ''.join(line for line in lines if line != addition) != resolved:
        return False
    return allow_unverified_sage or installed(paths)


def compatible_runtime(base, target):
    a,b=get_artifact_set(base),get_artifact_set(target)
    return a.runtime_id==b.runtime_id and a.runtime_sources==b.runtime_sources and a.tokenizers==b.tokenizers


def verify_provisioned(paths, profile, *, allow_unverified_sage=False):
    """Only the running application-owned environment, never a caller import path."""
    environment = Path(sys.prefix).absolute()
    relative = environment.relative_to(paths.environments).as_posix()
    if '/' in relative or len(relative) != 64 or any(c not in '0123456789abcdef' for c in relative):
        raise ValueError('application-owned digest environment required')
    with directory(paths, 'environments/' + relative):
        pass
    with regular_file(paths, f'environments/{relative}/resolved.txt') as stream:
        raw = stream.read(2 * 1024**2 + 1)
        if len(raw) > 2 * 1024**2:
            raise ValueError('resolved dependency record size limit')
        resolved = raw.decode()
    record = read_json(paths, f'environments/{relative}/runtime.json')
    if record != runtime_record(profile, resolved):
        base=record.get('profile')
        if record != runtime_record(base,resolved) or not compatible_runtime(base,profile):
            raise ValueError('runtime selection or dependency record mismatch')
        # Additional weights require their own exact license/artifact approval;
        # the base runtime receipt and frozen dependency contract stay immutable.
        from h3server.downloads import require_consent
        require_consent(paths,profile_name=profile)
    verify_sources(environment, profile)
    result = subprocess.run([sys.executable, '-m', 'pip', 'freeze', '--all'],
                            capture_output=True, timeout=60, check=True)
    if not dependencies_match(paths, resolved, result.stdout.decode(),
                              allow_unverified_sage=allow_unverified_sage):
        raise ValueError('installed dependencies differ from resolved environment')
    subprocess.run([sys.executable, '-m', 'pip', 'check'], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=60, check=True)
    return environment
