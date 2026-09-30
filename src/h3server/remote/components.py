"""Explicit desired-state reconciliation. No work is started by inventory/polling.

Only pinned, owned files are removed; directories containing unrelated files,
history, outputs, upload assets, environments and base dependencies are retained.
"""
import errno
import hashlib
import json
import os
from pathlib import Path
import threading
import uuid

from h3server.backends import load_manifest, turbo, sparse, reference, sage_attention
from h3server.backends.base import atomic_json, read_json, regular_file, directory, parts
from h3server.backends.catalog import PRUNED_PROFILE_NAMES, get_artifact_set
from h3server.backends.comfy_runtime import compatible_runtime
from h3server.downloads import record_consent, require_consent
from h3server.installer.diagnostics import safe_log

STATE='state/component-sync.json'
LOCK='state/component-sync.lock'
MODULES={'turbo':turbo,'sparse':sparse,'reference':reference}
OPTIONALS=('sageattention','turbo','sparse','reference')


def model_files(name):
    selected=get_artifact_set(name)
    return {f'models/h3-{selected.digest}/{a.local_relative_path}':(a.size_bytes,a.sha256) for a in selected.artifacts}


def partial_files(name):
    if name in PRUNED_PROFILE_NAMES:
        selected=get_artifact_set(name)
        return {f'tmp/downloads/{selected.digest}/{a.local_relative_path}.part':None for a in selected.artifacts}
    module=MODULES[name]
    return {f'tmp/{getattr(module,"COMPONENT",name)}-{module.DIGEST}/{e["name"]}.part':None for e in module.FILES}


def optional_files(name):
    module=MODULES[name]
    files={module.DIRECTORY+'/'+e['name']:(e['size'],e['sha256']) for e in module.FILES}
    if name=='sparse':files.update({module.DIRECTORY+'/code/'+e['name']:(e['size'],e['sha256']) for e in module.INVENTORY})
    return files


def inspect_file(paths,relative):
    """No symlink/hardlink/foreign-owner aliases, even during status inspection."""
    try:
        elements=parts(relative)
        for depth in range(len(elements)):
            with directory(paths,'/'.join(elements[:depth])) as parent:
                info=os.fstat(parent)
                if info.st_uid!=os.getuid() or info.st_mode & 0o022:raise ValueError('Managed component directory ownership or permissions changed')
        with regular_file(paths,relative) as stream:
            s=os.fstat(stream.fileno())
            if s.st_uid!=os.getuid():raise ValueError('Managed component owner changed')
            return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
    except FileNotFoundError:return None


def remove_files(paths,files):
    # Preflight the complete supplied set before unlinking any part of it.
    inventory={name:inspect_file(paths,name) for name in files}
    for relative,expected in inventory.items():
        if expected is None:continue
        elements=parts(relative)
        with directory(paths,'/'.join(elements[:-1])) as parent:
            with regular_file(paths,relative) as stream:
                s=os.fstat(stream.fileno())
                current=[s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
                if current!=expected:raise ValueError('Managed component changed during removal')
                entry=os.stat(elements[-1],dir_fd=parent,follow_symlinks=False)
                if (entry.st_dev,entry.st_ino)!=(s.st_dev,s.st_ino):raise ValueError('Managed component path changed')
                os.unlink(elements[-1],dir_fd=parent);os.fsync(parent)
        # Empty managed subdirectories only. Never recurse into/delete siblings.
        for depth in range(len(elements)-1,1,-1):
            try:
                with directory(paths,'/'.join(elements[:depth-1])) as fd:os.rmdir(elements[depth-1],dir_fd=fd)
            except FileNotFoundError:pass
            except OSError as e:
                if e.errno in (errno.ENOTEMPTY,errno.EEXIST):break
                raise


class Components:
    def __init__(self,worker,library,installers):
        self.worker=worker;self.paths=worker.paths;self.library=library;self.installers=installers
        self.lock=threading.RLock();self.thread=None;self.stop=threading.Event()
        self.instance=uuid.uuid4().hex

    def _read(self):
        try:return read_json(self.paths,STATE)
        except FileNotFoundError:return dict(state='idle',completed_actions=0,total_actions=0,actions=[],error=None)

    def _publish(self,**values):
        with self.lock:atomic_json(self.paths,STATE,{**self._read(),**values})

    def inventory(self):
        rows=[];signatures={}
        registered=self.library._registered(compatible_only=False) if self.library.supported else {}
        supported=self.library.supported
        for name in PRUNED_PROFILE_NAMES:
            selected=get_artifact_set(name);files=model_files(name)
            stats={p:inspect_file(self.paths,p) for p in files}
            partials={p:inspect_file(self.paths,p) for p in partial_files(name)};signatures[name]={**stats,**partials}
            approved=True
            try:require_consent(self.paths,profile_name=name)
            except (OSError,ValueError):approved=False
            ready=approved and all(s is not None and s[2]==files[p][0] for p,s in stats.items())
            compatible=supported and compatible_runtime(self.library.profile,name)
            rows.append(dict(id=name,kind='model',installed=ready,present=bool(any(stats.values()) or any(partials.values()) or name in registered),
                can_install=compatible,can_remove=supported,artifact_digest=selected.digest,
                total_bytes=selected.weight_bytes,reason='' if compatible else 'Requires a different runtime; batch runtime switching is not supported.'))
        for name in OPTIONALS:
            installer=self.installers[name];data=installer.status()
            module=sage_attention if name=='sageattention' else MODULES[name]
            files={} if name=='sageattention' else optional_files(name)
            stats={p:inspect_file(self.paths,p) for p in files}
            if name in MODULES:stats.update({p:inspect_file(self.paths,p) for p in partial_files(name)})
            stats[module.RECEIPT]=inspect_file(self.paths,module.RECEIPT)
            if name=='sageattention' and installer._read().get('state')=='removing':stats['uninstall_pending']=True
            signatures[name]=stats
            required=False
            if name=='sageattention' and (data['installed'] or any(stats.values())):
                import sys
                try:
                    relative=Path(sys.prefix).relative_to(self.paths.root).as_posix()+'/resolved.txt'
                    with regular_file(self.paths,relative) as f:required=f'sageattention=={sage_attention.VERSION}' in f.read(2*1024**2).decode().splitlines()
                except (ValueError,OSError):required=True
            rows.append(dict(id=name,kind='optional',installed=data['installed'],present=bool(data['installed'] or any(stats.values())),
                required=required,can_install=supported,can_remove=supported and not required,artifact_digest=getattr(module,'DIGEST',sage_attention.WHEEL_SHA256),
                total_bytes=data.get('total',0),reason='Required by the frozen base runtime; cannot be removed separately.' if required else '' if supported else 'Requires a managed pruned runtime.'))
        identity={'schema':1,'instance':self.instance,'runtime':self.library.profile,'environment':self.library.environment,
                  'items':rows,'files':signatures}
        digest=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        return dict(schema=1,supported=bool(supported),digest=digest,items=rows)

    def status(self):
        with self.lock:
            self.recover()
            state=self._read();busy=bool(self.thread and self.thread.is_alive()) or state['state']=='running'
            result={**state,'busy':busy}
            current=state.get('current')
            if busy and current and current['action']=='install':
                installer=self.library if current['id'] in PRUNED_PROFILE_NAMES else self.installers[current['id']]
                child=installer._read()
                result['current_completed']=child.get('completed',0);result['current_total']=child.get('total',0)
                result['current_phase']=child.get('state','installing')
            return result

    def recover(self):
        import fcntl
        with self.lock:
            if self.thread and self.thread.is_alive():return
            state=self._read()
            if state['state']!='running':return
            with regular_file(self.paths,LOCK,flags=os.O_CREAT|os.O_RDWR,mode='r+b',create_parents=True) as guard:
                try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:return
                maintenance=self.worker.maintenance_status()
                if maintenance.get('operation')==state.get('operation') and maintenance.get('active'):
                    self.worker.end_maintenance(state['operation'])
                self._publish(state='interrupted',error='Interrupted. Completed changes are retained; review the remaining changes and Apply again.')

    def start(self,desired,inventory_digest,license_sha256,license_acknowledged,eligibility_confirmed,download_approved):
        with self.lock:
            self.recover()
            if self.thread and self.thread.is_alive():raise ValueError('Component synchronization is busy')
            if not self.library.supported:raise ValueError('Component synchronization requires a managed pruned runtime')
            if not isinstance(desired,list) or len(desired)!=len(set(desired)):raise ValueError('Invalid desired selection')
            inventory=self.inventory();items={i['id']:i for i in inventory['items']}
            if set(desired)-items.keys():raise ValueError('Unknown component selection')
            if inventory_digest!=inventory['digest']:raise ValueError('Component inventory changed; refresh and review your selection')
            if license_sha256!=load_manifest()['sources']['license']['sha256'] or any(v is not True for v in (license_acknowledged,eligibility_confirmed,download_approved)):
                raise ValueError('Explicit pinned license, eligibility and download approval required')
            additions=[n for n in desired if not items[n]['installed']]
            removals=[n for n,i in items.items() if i['present'] and n not in desired]
            if any(not items[n]['can_install'] for n in additions):raise ValueError('Selected component requires a different runtime')
            if any(not items[n]['can_remove'] for n in removals):raise ValueError('Selected component is required by the runtime and cannot be removed separately')
            models=set(desired)&set(PRUNED_PROFILE_NAMES)
            active={p for p in models if compatible_runtime(self.library.profile,p)}
            for name in set(additions)&set(OPTIONALS):
                supported=active if name=='sageattention' else active & set(getattr(MODULES[name],'SUPPORTED_PROFILES',getattr(MODULES[name],'TURBO_PROFILES',getattr(MODULES[name],'PROFILES',()))))
                if not supported:raise ValueError('Keep a compatible model selected for '+name)
            import fcntl
            guard_context=regular_file(self.paths,LOCK,flags=os.O_CREAT|os.O_RDWR,mode='r+b',create_parents=True)
            guard=guard_context.__enter__()
            try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BaseException:
                guard_context.__exit__(None,None,None)
                raise ValueError('Component synchronization is busy') from None
            operation=uuid.uuid4().hex
            try:
                actions=[{'id':n,'action':'install'} for n in additions if n in PRUNED_PROFILE_NAMES]
                actions += [{'id':n,'action':'install'} for n in additions if n in OPTIONALS]
                actions += [{'id':n,'action':'remove'} for n in removals if n in OPTIONALS]
                actions += [{'id':n,'action':'remove'} for n in removals if n in PRUNED_PROFILE_NAMES]
                approval=dict(license_sha256=license_sha256,license_acknowledged=True,eligibility_confirmed=True,download_approved=True)
                # Journal ownership before taking the durable gate: a crash cannot
                # leave an unidentified maintenance owner. The guard transfers to
                # the thread and mutating subprocesses without an unlock window.
                self._publish(state='running',operation=operation,desired=desired,actions=actions,current=None,
                              completed_actions=0,total_actions=len(actions),stop_requested=False,error=None)
                self.worker.begin_maintenance(operation,policy='reject')
                if self.inventory()['digest']!=inventory_digest:raise ValueError('Component inventory changed')
                registered={n:get_artifact_set(n).digest for n,i in items.items() if n in PRUNED_PROFILE_NAMES and i['installed']}
                atomic_json(self.paths,self.library.registry,{'schema':2,'profiles':registered})
                with self.worker._lock:self.worker._drop_resident();self.library._refresh_backend()
                self.stop.clear();self.thread=threading.Thread(target=self._run,args=(operation,actions,desired,approval,guard_context,guard),daemon=True,name='h3-component-sync');self.thread.start()
            except BaseException as error:
                if self.worker.maintenance_status().get('operation')==operation:self.worker.end_maintenance(operation)
                self._publish(state='failed',error=safe_log(str(error))[:2000])
                guard_context.__exit__(None,None,None)
                raise
            return self.status()

    def _install(self,name,operation,approval,guard):
        if name in PRUNED_PROFILE_NAMES:
            installer=self.library
            record_consent(self.paths,profile_name=name,artifact_set_digest=get_artifact_set(name).digest,**approval)
            installer._publish(state='verifying',operation=None,profile=name,completed=0,total=get_artifact_set(name).weight_bytes,error=None)
            installer.stop=self.stop
            installer._run(name,operation,release=False)
        else:
            installer=self.installers[name]
            if name in MODULES:
                module=MODULES[name];atomic_json(self.paths,module.CONSENT,module.BINDING)
                installer.stop=self.stop
            installer._publish(state='installing',operation=None,completed=0,error=None)
            installer._run(operation,release=False,maintenance_fd=guard.fileno())
        state=installer._read()
        if state['state']!='installed':raise ValueError(state.get('error') or 'Component installation failed')

    def _remove(self,name,desired,guard):
        self._remove_derived(name,desired)
        if name in PRUNED_PROFILE_NAMES:
            remove_files(self.paths,{**model_files(name),**partial_files(name)})
            registered=self.library._registered(compatible_only=False);registered.pop(name,None)
            atomic_json(self.paths,self.library.registry,{'schema':2,'profiles':registered})
            with self.worker._lock:self.library._refresh_backend()
        elif name=='sageattention':
            self._remove_sage(guard)
        else:
            module=MODULES[name];files={**optional_files(name),**partial_files(name)}
            files[module.RECEIPT]=None
            if name=='sparse':files[sparse.GPU_RECEIPT]=None
            remove_files(self.paths,files)
            self.installers[name]._publish(state='not_installed',operation=None,completed=0,error=None)

    def _remove_derived(self,name,desired):
        from h3server.backends.safetensors_compat import REPAIRS
        if name in PRUNED_PROFILE_NAMES:artifacts=get_artifact_set(name).artifacts
        elif name=='reference':
            from types import SimpleNamespace
            artifacts=[SimpleNamespace(sha256=e['sha256'],local_relative_path=e['name']) for e in reference.FILES]
        else:return
        # Also preserve caches for removals not yet performed if a batch stops.
        present={n for n in PRUNED_PROFILE_NAMES if n!=name and any(inspect_file(self.paths,p) for p in model_files(n))}
        retained={a.sha256 for n in set(desired)|present if n in PRUNED_PROFILE_NAMES for a in get_artifact_set(n).artifacts}
        if 'reference' in desired or name!='reference' and any(inspect_file(self.paths,p) for p in optional_files('reference')):retained.update(e['sha256'] for e in reference.FILES)
        files={f'cache/safetensors-compat/{a.sha256}/{Path(a.local_relative_path).name}':None for a in artifacts if a.sha256 in REPAIRS and a.sha256 not in retained}
        remove_files(self.paths,files)

    def _remove_sage(self,guard):
        from h3server.backends.comfy_runtime import verify_provisioned
        from h3server.installer.steps import ProcessIO,environment
        import sys
        prefix=verify_provisioned(self.paths,self.library.profile,allow_unverified_sage=True)
        if f'sageattention=={sage_attention.VERSION}' in (prefix/'resolved.txt').read_text().splitlines():
            raise ValueError('SageAttention is part of the frozen base runtime and cannot be uninstalled separately')
        self.installers['sageattention']._publish(state='removing',operation=None,error=None)
        # Never leave an old GPU-verification receipt advertising a package
        # after a failed/interrupted uninstall. The durable marker allows retry.
        remove_files(self.paths,{sage_attention.RECEIPT:None})
        io=ProcessIO(command_timeout=300);io.lock_fd=guard.fileno()
        io.run((sys.executable,'-m','pip','uninstall','--yes','sageattention'),env=environment(self.paths),log=self.installers['sageattention'])
        verify_provisioned(self.paths,self.library.profile)
        self.installers['sageattention']._publish(state='not_installed',operation=None,error=None)

    def _run(self,operation,actions,desired,approval,guard_context,guard):
        try:
            for index,action in enumerate(actions):
                if self.stop.is_set():raise InterruptedError('Stopped; completed changes are retained.')
                self._publish(current=action,completed_actions=index)
                if action['action']=='install':self._install(action['id'],operation,approval,guard)
                else:self._remove(action['id'],desired,guard)
                self._publish(completed_actions=index+1)
            self._publish(state='completed',current=None,error=None)
        except Exception as error:
            self._publish(state='cancelled' if self.stop.is_set() else 'failed',error=safe_log(str(error))[:2000])
        finally:
            try:self.worker.end_maintenance(operation)
            finally:guard_context.__exit__(None,None,None)

    def cancel(self,operation):
        with self.lock:
            if self._read().get('operation')!=operation:raise ValueError('Synchronization ownership changed')
            self.stop.set();self._publish(stop_requested=True);return self.status()

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join()
