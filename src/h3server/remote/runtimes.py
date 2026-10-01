"""Verified per-model environment bindings; not a provisioner or an HTTP API.

Publication requires an explicit native probe in the selected environment. Reads
never provision, repair or select an alternative environment. The existing daemon
is not switched by publishing a binding.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import threading

from h3server.backends import get_profile, load_manifest
from h3server.backends.base import atomic_json, directory, regular_file
from h3server.backends.catalog import PRUNED_PROFILE_NAMES, get_artifact_set
from h3server.backends.comfy_runtime import provision_contract, runtime_record

_HEX = re.compile(r'[a-f0-9]{64}')
_LIMIT = 2 * 1024**2
_PROBE = '''
import json, sys
from pathlib import Path
from contextlib import redirect_stdout
from h3server.paths import AppPaths
from h3server.backends.catalog import PRUNED_PROFILE_NAMES
with redirect_stdout(sys.stderr):
    if sys.argv[2] in PRUNED_PROFILE_NAMES:
        from h3server.backends.pruned import runtime_diagnostic
        report = runtime_diagnostic(AppPaths(Path(sys.argv[1])), sys.argv[2])
    else:
        from h3server.backends.h3 import runtime_diagnostic
        report = runtime_diagnostic()
report['environment'] = str(Path(sys.prefix).absolute())
print(json.dumps(report), flush=True)
'''


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _identifier(value):
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError('Invalid owned runtime environment identifier')
    return value


def _read(paths, relative):
    # The root and every parent must remain private to the owning account.
    elements = relative.split('/')
    for depth in range(len(elements)):
        with directory(paths, '/'.join(elements[:depth])) as fd:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise ValueError('Runtime directory ownership or permissions changed')
    with regular_file(paths, relative) as stream:
        info = os.fstat(stream.fileno())
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError('Runtime receipt ownership or permissions changed')
        raw = stream.read(_LIMIT + 1)
        if len(raw) > _LIMIT:
            raise ValueError('Runtime receipt size limit')
        return raw


def probe_environment(paths, profile, identifier, lock_fd):
    """Bounded fixed commands, inherited ownership, no installation or inference."""
    from h3server.installer.steps import ProcessIO, environment
    class Quiet:
        def write(self, text): pass
        def flush(self): pass
    io = ProcessIO(command_timeout=180)
    io.lock_fd = lock_fd
    python = str(paths.environments / _identifier(identifier) / 'bin/python')
    env = environment(paths)
    if profile not in PRUNED_PROFILE_NAMES:
        frozen = io.run((python, '-m', 'pip', 'freeze', '--all'), env=env, log=Quiet(), capture=True)
        if frozen != _read(paths, f'environments/{identifier}/resolved.txt').decode():
            raise ValueError('Runtime dependencies changed')
    output = io.run((python, '-c', _PROBE, str(paths.root), profile), env=env, log=Quiet(), capture=True)
    try:
        return json.loads(output.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise ValueError('Runtime verification returned no valid report') from None


class RuntimeBindings:
    registry = 'config/runtime-bindings.json'
    guard = 'state/runtime-bindings.lock'

    def __init__(self, paths):
        self.paths = paths
        self.lock = threading.RLock()

    @staticmethod
    def family(profile):
        get_profile(profile)
        if profile in PRUNED_PROFILE_NAMES:
            selected = get_artifact_set(profile)
            # Weight sizes/digests differ within a family; source/tokenizer and
            # dependency contracts, not those sizes, define runtime reuse.
            from h3server.installer.planner import requirements
            contract = provision_contract(profile)
            return _digest({'runtime': selected.runtime_id, 'sources': contract['sources'],
                            'tokenizers': [vars(item) for item in selected.tokenizers],
                            'requirements': requirements(profile)})
        manifest = load_manifest()
        return _digest({'runtime': 'legacy-h3', 'backend': manifest['backend'],
                        'dependencies': manifest['candidate_environment']})

    def _bindings(self):
        try:
            data = json.loads(_read(self.paths, self.registry))
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict) or set(data) != {'schema', 'profiles'} or data['schema'] != 1 or not isinstance(data['profiles'], dict):
            raise ValueError('Invalid runtime binding registry')
        for profile, binding in data['profiles'].items():
            get_profile(profile)
            if not isinstance(binding, dict) or set(binding) != {'environment', 'family', 'fingerprint', 'artifact'}:
                raise ValueError('Invalid runtime binding')
            for field in ('environment', 'family', 'fingerprint'):
                _identifier(binding[field])
        return data['profiles']

    def _snapshot(self, profile, identifier, artifact):
        _identifier(identifier)
        family = self.family(profile)
        prefix = f'environments/{identifier}'
        names = [f'config/installation-{identifier}.json', prefix + '/resolved.txt', prefix + '/readiness.json']
        raw = {name: _read(self.paths, name) for name in names}
        installation = json.loads(raw[names[0]])
        if installation.get('environment') != str(self.paths.environments / identifier):
            raise ValueError('Runtime environment receipt mismatch')
        if self.family(installation.get('profile')) != family:
            raise ValueError('Runtime family mismatch')
        if installation.get('artifact') != artifact or not isinstance(artifact, dict):
            raise ValueError('Runtime release binding changed')
        if set(artifact) != {'release_id', 'filename', 'sha256'}:
            raise ValueError('Invalid runtime release binding')
        _identifier(artifact['sha256'])
        if json.loads(raw[names[2]]).get('environment_ready') is not True:
            raise ValueError('Runtime environment verification is incomplete')
        if profile in PRUNED_PROFILE_NAMES:
            name = prefix + '/runtime.json'
            raw[name] = _read(self.paths, name)
            if json.loads(raw[name]) != runtime_record(installation['profile'], raw[names[1]].decode()):
                raise ValueError('Runtime dependency contract changed')
        return {'environment': identifier, 'family': family, 'artifact': artifact,
                'fingerprint': _digest({name: hashlib.sha256(value).hexdigest() for name, value in raw.items()})}

    def resolve(self, profile):
        get_profile(profile)
        binding = self._bindings().get(profile)
        if binding is None:
            return None
        if binding != self._snapshot(profile, binding['environment'], binding['artifact']):
            raise ValueError('Runtime binding changed; explicit verification required')
        return {**binding, 'python': str(self.paths.environments / binding['environment'] / 'bin/python')}

    def publish(self, profile, identifier, *, artifact):
        """Called by the maintenance owner after provisioning, never by inventory."""
        import fcntl
        before = self._snapshot(profile, identifier, artifact)
        with self.lock, regular_file(self.paths, self.guard, flags=os.O_CREAT | os.O_RDWR,
                                     mode='r+b', create_parents=True) as guard:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            bindings = self._bindings()
            report = probe_environment(self.paths, profile, identifier, guard.fileno())
            if not isinstance(report, dict) or report.get('environment_ready') is not True or report.get('environment') != str(self.paths.environments / identifier):
                raise ValueError('Selected runtime native verification failed')
            if before != self._snapshot(profile, identifier, artifact):
                raise ValueError('Runtime changed during verification')
            bindings[profile] = before
            atomic_json(self.paths, self.registry, {'schema': 1, 'profiles': bindings})
        return self.resolve(profile)
