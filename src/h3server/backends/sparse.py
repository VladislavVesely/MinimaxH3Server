"""Pinned existing-weight sparse attention. No dependency upgrades or plugin discovery."""
import hashlib
import importlib.abc
import importlib.util
from importlib import metadata, resources
import json
import os
from pathlib import Path
import sys
import zipfile

from h3server.backends.base import atomic_json, read_json, regular_file
from h3server.backends.turbo import _verify, download as transfer

COMPONENT = 'sparse'
REVISION = '862774944a331bc1a66cee1cf805b94bd138ebbe'
FILES = [dict(name='source.zip', size=21870839, sha256='83655b4ddc10ff832ab9eeedd27ac585c2ec8dede04332ae31cb050d878e6bb3',
              url=f'https://codeload.github.com/Zironic/H3-Optimizations/zip/{REVISION}')]
INVENTORY = json.loads(resources.files('h3server.backends').joinpath('sparse-inventory.json').read_text())
PRESETS = {'quality': .5, 'balanced': .3, 'draft': .15}
DIGEST = hashlib.sha256(json.dumps([FILES, INVENTORY, PRESETS, {'denser_early_ramp': True, 'turbo_compatible': False}], sort_keys=True).encode()).hexdigest()
DIRECTORY = 'components/h3-sparse-' + DIGEST
RECEIPT = 'state/sparse-download-receipt.json'
GPU_RECEIPT = 'state/sparse-gpu-receipt.json'
CONSENT = 'config/sparse-consent.json'
TOTAL = FILES[0]['size']
PROFILES = ('h3-convrot-int8',)
BINDING = dict(artifact_digest=DIGEST, revision=REVISION, approved=True)


def require_consent(paths):
    if read_json(paths, CONSENT) != BINDING:
        raise ValueError('Explicit pinned sparse download consent required')


def gpu_receipt_path(paths, environment=None):
    from h3server.backends.environment_scope import receipt_path
    return receipt_path(paths, GPU_RECEIPT, environment)


def gpu_binding(paths=None, environment=None):
    if paths is None:
        return dict(artifact_digest=DIGEST, environment=str(Path(sys.prefix).resolve()), torch=metadata.version('torch'), gpu_probe=True)
    from h3server.backends.environment_scope import prefix, torch_version
    return dict(artifact_digest=DIGEST, environment=str(prefix(paths, environment)), torch=torch_version(paths, environment), gpu_probe=True)


def verify_code(paths, *, verify=True):
    require_consent(paths)
    if read_json(paths, RECEIPT) != BINDING:
        raise ValueError('Sparse download receipt missing')
    signatures = {}
    for entry in INVENTORY:
        with regular_file(paths, DIRECTORY + '/code/' + entry['name']) as f:
            if verify:
                _verify(f, entry)
            elif os.fstat(f.fileno()).st_size != entry['size']:
                raise ValueError('Sparse component size changed')
            info = os.fstat(f.fileno())
            signatures[entry['name']] = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
    return signatures


def installed(paths, *, verify=False, environment=None):
    try:
        from h3server.backends.environment_scope import receipt
        signatures = verify_code(paths, verify=verify)
        return receipt(paths, GPU_RECEIPT, environment) == {'runtime': gpu_binding() if environment is None else gpu_binding(paths, environment), 'files': signatures}
    except (OSError, ValueError, metadata.PackageNotFoundError):
        return False


def require(paths, digest, *, environment=None):
    if digest != DIGEST or not installed(paths, verify=True, environment=environment):
        raise ValueError('Sparse preset is missing, changed or not GPU-verified; install its optional component')


def download(paths, *, progress, cancelled=lambda: False):
    transfer(paths, progress=progress, cancelled=cancelled, _component=sys.modules[__name__])
    with regular_file(paths, DIRECTORY + '/source.zip') as f:
        _verify(f, FILES[0])
        with zipfile.ZipFile(f) as archive:
            prefix = 'H3-Optimizations-' + REVISION + '/'
            for entry in INVENTORY:
                if cancelled():
                    raise InterruptedError('Sparse installation cancelled')
                info = archive.getinfo(prefix + entry['name'])
                if info.file_size != entry['size'] or (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('Invalid sparse archive member')
                data = archive.read(info)
                if hashlib.sha256(data).hexdigest() != entry['sha256']:
                    raise ValueError('Sparse archive member checksum mismatch')
                # Only source-owned inventory names are extracted, never archive-selected paths.
                with regular_file(paths, DIRECTORY + '/code/' + entry['name'], flags=os.O_CREAT | os.O_WRONLY | os.O_TRUNC,
                                  mode='wb', create_parents=True) as out:
                    out.write(data); out.flush(); os.fsync(out.fileno())
    verify_code(paths)


class VerifiedLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Execute exact source bytes, including submodules; never load cached pyc."""
    def __init__(self, paths):
        self.paths = paths
        self.entries = {e['name']: e for e in INVENTORY}

    def find_spec(self, fullname, path=None, target=None):
        if fullname != 'h3_optimizations' and not fullname.startswith('h3_optimizations.'):
            return None
        rel = fullname.replace('.', '/')
        package = rel + '/__init__.py' in self.entries
        filename = rel + ('/__init__.py' if package else '.py')
        if filename not in self.entries:
            raise ImportError('Unpinned sparse module: ' + fullname)
        spec = importlib.util.spec_from_loader(fullname, self, is_package=package)
        spec.origin = str(self.paths.root / DIRECTORY / 'code' / filename)
        spec.loader_state = filename
        return spec

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        name = module.__spec__.loader_state
        with regular_file(self.paths, DIRECTORY + '/code/' + name) as f:
            _verify(f, self.entries[name]); f.seek(0); source = f.read(self.entries[name]['size'] + 1)
        module.__file__ = module.__spec__.origin
        if module.__spec__.submodule_search_locations is not None:
            module.__path__ = [str(Path(module.__file__).parent)]
        exec(compile(source, module.__file__, 'exec'), module.__dict__)
        if module.__name__ == 'h3_optimizations.native.loader':
            # Upstream supports a developer library_path.txt and local builds.
            # This integration authorizes only the checksum-verified shipped binary.
            module._candidate_paths = lambda: [self.paths.root / DIRECTORY / 'code/native/bin/libh3_int8_attention.so']


def load(paths):
    verify_code(paths)
    if any(name == 'h3_optimizations' or name.startswith('h3_optimizations.') for name in sys.modules):
        raise ValueError('An unowned sparse package is already loaded')
    sys.meta_path.insert(0, VerifiedLoader(paths))
    from h3_optimizations.nodes import H3SparseAttention
    return H3SparseAttention


def apply(model, settings, node):
    if settings.turbo_lora:
        raise ValueError('Sparse + Turbo is blocked: upstream sparse QKV execution bypasses Turbo adapters')
    return node.execute(model, video_budget=PRESETS[settings.sparse_preset], denser_early_late_steps=True)[0]


def trace_execution():
    # Instrument successful native sparse launches, not a requested configuration.
    from h3_optimizations.native import int8_attention, selftest
    original = getattr(int8_attention, '_h3_trace_original', int8_attention._check_sparse_attention)
    int8_attention._h3_trace_original = original
    int8_attention._check_sparse_attention = original
    if not selftest.check(force=True):  # Never count/cache a self-test as generation evidence.
        raise RuntimeError('Sparse native GPU recheck failed; disable sparse to retain the normal attention path')
    counts = {'native_sparse_calls': 0}
    def traced(*args):
        result = original(*args)
        counts['native_sparse_calls'] += 1
        return result
    int8_attention._check_sparse_attention = traced
    return counts


def probe(paths):
    from h3server.backends.comfy_runtime import verify_provisioned
    environment = verify_provisioned(paths, PROFILES[0])
    sys.path.insert(0, str(environment / 'ComfyUI'))
    sys.dont_write_bytecode = True
    import comfy.options
    comfy.options.enable_args_parsing(False)
    from comfy.cli_args import args
    args.disable_dynamic_vram = True
    args.base_directory = str(paths.tmp / 'comfy')
    load(paths)
    from h3_optimizations.native import selftest
    if not selftest.check(force=True):
        raise RuntimeError('Sparse native GPU numerical check failed')
    atomic_json(paths, gpu_receipt_path(paths), {'runtime': gpu_binding(paths), 'files': verify_code(paths)})
    print('Sparse native CUDA kernels passed numerical validation; base dependencies unchanged', flush=True)


if __name__ == '__main__':
    from h3server.paths import AppPaths
    probe(AppPaths(Path(sys.argv[1])))
