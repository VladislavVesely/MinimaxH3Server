"""Environment-local optional receipts, with exact-bound legacy read fallback."""
from pathlib import Path
import re
import sys
from h3server.backends.base import directory, regular_file, read_json


def prefix(paths, environment=None):
    value=Path(sys.prefix if environment is None else environment).absolute()
    try:
        identifier=value.relative_to(paths.environments).as_posix()
        if not re.fullmatch('[a-f0-9]{64}',identifier):raise ValueError('Invalid environment')
        with directory(paths,'environments/'+identifier):pass
    except ValueError:
        if environment is not None:raise
    return value


def receipt_path(paths, name, environment=None):
    value=prefix(paths,environment)
    try:identifier=value.relative_to(paths.environments).as_posix()
    except ValueError:return name
    if not re.fullmatch('[a-f0-9]{64}',identifier):return name
    return 'state/runtime-components/'+identifier+'/'+Path(name).name


def receipt(paths, name, environment=None):
    scoped=receipt_path(paths,name,environment)
    try:return read_json(paths,scoped)
    except FileNotFoundError:
        if scoped==name:raise
        result=read_json(paths,name)
        binding=result.get('runtime',result)
        if binding.get('environment')!=str(prefix(paths,environment)):
            raise ValueError('Optional receipt belongs to a different runtime')
        return result


def torch_version(paths, environment=None):
    if environment is None or prefix(paths,environment)==Path(sys.prefix).absolute():
        from importlib.metadata import version
        return version('torch')
    relative=prefix(paths,environment).relative_to(paths.root).as_posix()+'/resolved.txt'
    with regular_file(paths,relative) as f:raw=f.read(2*1024**2+1)
    if len(raw)>2*1024**2:raise ValueError('Dependency record size limit')
    matches=[line.split('==',1)[1] for line in raw.decode().splitlines() if line.lower().startswith('torch==')]
    if len(matches)!=1:raise ValueError('Missing frozen Torch version')
    return matches[0]
