"""Pinned optional Turbo v4-600 LoRA + upstream node, separate from base files.

No plugin discovery, dependency installation, runtime downloads or caller URLs.
Upstream code/grid/LICENSE are fetched only by explicit optional installation.
"""
import hashlib
import sys
import types
import json
import os
from pathlib import Path
import time

from h3server.backends.base import atomic_json, read_json, regular_file, directory
from h3server.domain import TURBO_PROFILES

REVISION = '43a74557ac3f6539db8e0f2a959d03feb7a81480'
NODE_REVISION = '4274783a23afcfdbea3b4876cb79effd6c510785'
WEIGHT = 'minimax_h3_turbo_v4_step600_ema.safetensors'
FILES = [
    dict(name=WEIGHT, size=779849816, sha256='5f3a626cd72c93a8b9318d6760c510bc5092d2ab13aaba1f932c5bab07a416d3',
         url=f'https://huggingface.co/larryvrh/MiniMax-H3-Turbo-Lora/resolve/{REVISION}/{WEIGHT}'),
    dict(name='__init__.py', size=26363, sha256='a22f222f11dfc337552371f7f288bf5928cb1fa80a6afb3f24075a8315d02f06'),
    dict(name='h3_silu_temb_grid.safetensors', size=5510600, sha256='30eb3c2cc7fb6b470d9717ff840d359313ac27cd64b705e32da1baa10f72d6a8'),
    dict(name='LICENSE', size=11358, sha256='cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30'),
]
for _entry in FILES[1:]:
    _entry['url'] = f'https://raw.githubusercontent.com/Larryvrh/ComfyUI-MiniMax-H3-Turbo/{NODE_REVISION}/{_entry["name"]}'
DIGEST = hashlib.sha256(json.dumps(FILES, sort_keys=True).encode()).hexdigest()
DIRECTORY = 'loras/h3-turbo-' + DIGEST
RECEIPT = 'state/turbo-receipt.json'
CONSENT = 'config/turbo-consent.json'
TOTAL = sum(e['size'] for e in FILES)
BINDING = dict(artifact_digest=DIGEST, revision=REVISION, node_revision=NODE_REVISION,
               license='Apache-2.0', approved=True)


def require_consent(paths):
    if read_json(paths, CONSENT) != BINDING:
        raise ValueError('Explicit pinned Turbo download consent required')


class ArtifactMismatch(ValueError):
    pass


def _verify(stream, entry):
    if os.fstat(stream.fileno()).st_size != entry['size']:
        raise ArtifactMismatch('Pinned component artifact size mismatch')
    stream.seek(0)
    if hashlib.file_digest(stream, 'sha256').hexdigest() != entry['sha256']:
        raise ArtifactMismatch('Pinned component artifact checksum mismatch')


def installed(paths, *, verify=False):
    try:
        require_consent(paths)
        if read_json(paths, RECEIPT) != BINDING:
            return False
        for entry in FILES:
            with regular_file(paths, DIRECTORY + '/' + entry['name']) as stream:
                if verify:
                    _verify(stream, entry)
                elif os.fstat(stream.fileno()).st_size != entry['size']:
                    return False
        return True
    except (OSError, ValueError):
        return False


def require(paths, digest=None):
    if digest is not None and digest != DIGEST:
        raise ValueError('Requested Turbo artifact set differs from the installed preset')
    if not installed(paths, verify=True):
        raise ValueError('Turbo LoRA requested but its pinned files are missing or invalid; install the optional component')


def download(paths, *, progress, cancelled=lambda: False, _component=None):
    from h3server._download_transport import DownloadTransport, CHUNK
    component = _component or sys.modules[__name__]
    component.require_consent(paths)
    completed = 0
    for entry in component.FILES:
        component.require_consent(paths)
        name = entry['name']
        target = component.DIRECTORY + '/' + name
        partial = 'tmp/' + getattr(component, 'COMPONENT', 'turbo') + '-' + component.DIGEST + '/' + name + '.part'
        try:
            with regular_file(paths, target) as stream: _verify(stream, entry)
        except (FileNotFoundError, ArtifactMismatch):
            pass
        else:
            completed += entry['size']; progress(completed, component.TOTAL); continue
        with regular_file(paths, partial, flags=os.O_CREAT | os.O_RDWR, mode='r+b', create_parents=True) as stream:
            offset = os.fstat(stream.fileno()).st_size
            if offset > entry['size']: stream.truncate(0); offset = 0
            if offset < entry['size']:
                with DownloadTransport(paths, name, offset, timeout=30, deadline=time.monotonic()+6*3600,
                                       cancelled=cancelled, component=getattr(component, 'COMPONENT', 'turbo')) as response:
                    if response.status == 200:
                        stream.seek(0); stream.truncate(0); offset = 0
                    elif response.status != 206 or not offset or response.headers.get('Content-Range') != f"bytes {offset}-{entry['size']-1}/{entry['size']}":
                        raise ValueError('Unexpected optional component resume response')
                    remaining = entry['size'] - offset
                    if response.headers.get('Content-Length', str(remaining)) != str(remaining) or response.headers.get('Content-Encoding', 'identity') != 'identity':
                        raise ValueError('Unexpected optional component transfer length/encoding')
                    stream.seek(offset); progress(completed + offset, component.TOTAL)
                    while True:
                        chunk = response.read(min(CHUNK, remaining + 1))
                        if not chunk: break
                        if len(chunk) > remaining: raise ValueError('Optional component transfer exceeds pinned size')
                        stream.write(chunk); remaining -= len(chunk)
                        progress(completed + entry['size'] - remaining, component.TOTAL)
                    if remaining: raise ValueError('Truncated optional component download; retry to resume')
            stream.flush(); os.fsync(stream.fileno())
            try: _verify(stream, entry)
            except ValueError:
                stream.truncate(0)
                raise
        with directory(paths, str(Path(partial).parent)) as src, directory(paths, component.DIRECTORY, create=True) as dest:
            os.replace(Path(partial).name, name, src_dir_fd=src, dst_dir_fd=dest); os.fsync(dest)
        completed += entry['size']; progress(completed, component.TOTAL)
    atomic_json(paths, component.RECEIPT, component.BINDING)


def load_node(paths, digest=None):
    require(paths, digest)
    # Execute the verified source bytes, never an unchecked cached .pyc.
    with regular_file(paths, DIRECTORY + '/__init__.py') as stream:
        source = stream.read(FILES[1]['size'] + 1)
    if hashlib.sha256(source).hexdigest() != FILES[1]['sha256']:
        raise ValueError('Turbo node source changed')
    module = types.ModuleType('_h3_pinned_turbo')
    module.__file__ = str(paths.root / DIRECTORY / '__init__.py')
    sys.modules[module.__name__] = module
    exec(compile(source, module.__file__, 'exec'), module.__dict__)
    import folder_paths
    folder_paths.folder_names_and_paths['loras'] = ([str(paths.root / DIRECTORY)], {'.safetensors'})
    return module


def apply(model, settings, module):
    patched = module.MiniMaxH3TurboLoRA().apply_lora(model, WEIGHT, settings.turbo_strength,
                                                  low_vram=settings.turbo_low_vram)[0]
    active = patched.patches if settings.turbo_low_vram else patched.injections.get('bypass_lora')
    if not active:
        raise ValueError('Turbo LoRA did not attach its backbone updates')
    if getattr(model.model.diffusion_model, 'use_adaln_curves', False) and not any(
            'adaln_proj' in key and key.endswith('.forward') for key in patched.object_patches):
        raise ValueError('Turbo LoRA did not attach pruned time-conditioning updates')
    return patched


def sample(model, positive, latent, settings, module):
    from comfy_extras.nodes_custom_sampler import BasicScheduler, CFGGuider, RandomNoise, SamplerCustomAdvanced
    sigmas = BasicScheduler.execute(model, 'simple', settings.steps, 1.0)[0]
    guider = CFGGuider.execute(model, positive, [], 1.0)[0]
    noise = RandomNoise.execute(settings.seed)[0]
    sampler = module.MiniMaxH3TurboSampler().get_sampler()[0]
    return SamplerCustomAdvanced.execute(noise, guider, sampler, sigmas, latent)[0]
