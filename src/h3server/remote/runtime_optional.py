"""Fixed optional-component operation inside a selected owned interpreter."""
import argparse
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True);parser.add_argument('--profile',required=True)
    parser.add_argument('--component',choices=('sageattention','sparse'),required=True)
    parser.add_argument('--action',choices=('install','remove'),default='install')
    parser.add_argument('--operation',required=True);parser.add_argument('--lock-fd',type=int,required=True)
    args=parser.parse_args()
    from h3server.paths import AppPaths
    from h3server.backends.base import read_json
    from h3server.backends.catalog import PRUNED_PROFILE_NAMES
    from h3server.backends.comfy_runtime import verify_provisioned
    paths=AppPaths(Path(args.root))
    if args.profile not in PRUNED_PROFILE_NAMES or not re.fullmatch('[a-f0-9]{32}',args.operation):raise ValueError('Invalid runtime operation')
    state=read_json(paths,'state/maintenance.json')
    if state.get('operation')!=args.operation or state.get('active') is not True:raise ValueError('Runtime maintenance ownership changed')
    from h3server.remote.components import LOCK
    from h3server.backends.base import regular_file
    with regular_file(paths,LOCK) as owner:
        current=os.fstat(args.lock_fd);expected=os.fstat(owner.fileno())
        if (current.st_dev,current.st_ino)!=(expected.st_dev,expected.st_ino):raise ValueError('Wrong inherited maintenance guard')
    worker=SimpleNamespace(paths=paths,backend=SimpleNamespace(profiles=(args.profile,)))
    if args.action=='remove':
        if args.component!='sageattention':raise ValueError('Only an optional package is removed per environment')
        from h3server.backends import sage_attention as sage
        from h3server.backends.environment_scope import receipt_path
        from h3server.remote.components import remove_files
        from h3server.installer.steps import ProcessIO,environment
        prefix=verify_provisioned(paths,args.profile,allow_unverified_sage=True)
        if f'sageattention=={sage.VERSION}' in (prefix/'resolved.txt').read_text().splitlines():raise ValueError('SageAttention is a frozen base dependency')
        names={receipt_path(paths,sage.RECEIPT):None}
        try:
            if read_json(paths,sage.RECEIPT).get('environment')==str(prefix):names[sage.RECEIPT]=None
        except FileNotFoundError:pass
        remove_files(paths,names)
        io=ProcessIO(command_timeout=300);io.lock_fd=args.lock_fd
        io.run((sys.executable,'-m','pip','uninstall','--yes','sageattention'),env=environment(paths),log=sys.stderr)
        verify_provisioned(paths,args.profile)
        return
    if args.component=='sageattention':
        from h3server.remote.features import SageInstaller
        installer=SageInstaller(worker)
    else:
        from h3server.remote.sparse import SparseInstaller
        installer=SparseInstaller(worker)
    installer._run(args.operation,release=False,maintenance_fd=args.lock_fd)
    if installer._read()['state']!='installed':raise ValueError(installer._read().get('error') or 'Optional installation failed')


if __name__=='__main__':main()
