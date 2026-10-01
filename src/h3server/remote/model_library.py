"""Model installation and execution across isolated verified runtime families."""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid

from h3server.backends.base import atomic_json, read_json, regular_file
from h3server.backends.catalog import PRUNED_PROFILE_NAMES
from h3server.backends.comfy_runtime import compatible_runtime
from h3server.downloads import record_consent, require_consent, download_model, verify_snapshot
from h3server.installer.diagnostics import safe_log
from h3server.remote.model_catalog import MODEL_NAMES, selection as get_artifact_set
from h3server.remote.runtimes import RuntimeBindings
from h3server.remote.runtime_setup import RuntimeSetup

STATE='state/model-library-install.json'
LOCK='state/model-library-install.lock'


class ModelLibrary:
    def __init__(self, worker):
        self.worker=worker; self.paths=worker.paths; self.base=worker.backend
        self.profile=next(iter(self.base.profiles),''); self.environment=Path(sys.prefix).name
        self.registry='config/model-library.json'
        self.lock=threading.RLock();self.thread=None;self.stop=threading.Event();self.last_progress=0
        self.runtimes=RuntimeBindings(self.paths);self.runtime_setup=RuntimeSetup(self.paths,self.runtimes)
        self._children={self.profile:self.base};self.runtime_errors={}
        owned=(self.paths.root/f'config/installation-{self.environment}.json').is_file()
        self.supported=(self.profile in PRUNED_PROFILE_NAMES and self.base.resident) or (self.profile in MODEL_NAMES and owned)
        if self.supported:self._refresh_backend()

    def compatible(self,name):
        if self.profile in PRUNED_PROFILE_NAMES and name in PRUNED_PROFILE_NAMES:
            return compatible_runtime(self.profile,name)
        return self.runtimes.family(self.profile)==self.runtimes.family(name)

    def python_for_profile(self,name):
        if self.compatible(name):return sys.executable
        try:
            binding=self.runtimes.resolve(name)
            return binding['python'] if binding else None
        except (ValueError,OSError) as error:
            self.runtime_errors[name]=safe_log(str(error))[:500]
            return None

    def runtime_targets(self, component):
        from h3server.backends import sparse
        allowed=set(PRUNED_PROFILE_NAMES) if component=='sageattention' else set(sparse.PROFILES)
        names={self.profile,*self._registered(False),*self.runtimes._bindings()}
        targets={}
        for name in sorted(names & allowed):
            python=self.python_for_profile(name)
            if python:targets.setdefault(python,name)
        return targets

    def optional_profiles(self, component):
        from h3server.backends import sage_attention, sparse
        module=sage_attention if component=='sageattention' else sparse
        result={}
        allowed=PRUNED_PROFILE_NAMES if component=='sageattention' else sparse.PROFILES
        for name in self.worker.backend.profiles:
            if name not in allowed:continue
            python=self.python_for_profile(name)
            result[name]=bool(python) and (module.installed(self.paths) if python==sys.executable else module.installed(self.paths,environment=Path(python).parent.parent))
        return result

    def _registered(self,compatible_only=True):
        try:data=read_json(self.paths,self.registry)
        except FileNotFoundError:return {}
        if data.get('schema') not in (1,2) or not isinstance(data.get('profiles'),dict):raise ValueError('invalid model library binding')
        for name,digest in data['profiles'].items():
            if digest!=get_artifact_set(name).digest:raise ValueError('model library selection changed')
            require_consent(self.paths,profile_name=name)
        return {name:digest for name,digest in data['profiles'].items() if not compatible_only or self.python_for_profile(name)}

    def _refresh_backend(self):
        from h3server.backends.pruned import backend
        from h3server.backends import select_backend
        from h3server.backends.h3 import RuntimeReadiness
        try:managed=read_json(self.paths,self.registry).get('schema')==2
        except FileNotFoundError:managed=False
        profiles=tuple(self._registered()) if managed else tuple(dict.fromkeys([self.profile,*self._registered()]))
        children={}
        for name in profiles:
            python=self.python_for_profile(name)
            old=self._children.get(name)
            if old is not None and (python==sys.executable and self.compatible(name) or old.command[0]==python):
                children[name]=old;continue
            if name in PRUNED_PROFILE_NAMES:
                children[name]=backend(self.paths,name) if python==sys.executable else backend(self.paths,name,python=python)
            else:
                child=select_backend(self.paths,profile=name)
                command=(python,'-m','h3server.backends.h3')
                ready=RuntimeReadiness(self.paths,profile=name,command=command)
                children[name]=replace(child,command=command,readiness=ready,diagnostics=ready.diagnostics)
        self._children=children
        primary=children.get(self.profile,next(iter(children.values()),self.base))
        def selected(name):
            if name not in children or not self.python_for_profile(name):raise ValueError('Model runtime is unavailable; install or repair its environment')
            return children[name]
        def validate(request):selected(request.settings.profile).validate_request(request)
        self.worker.backend=replace(primary,profiles=profiles,validate_request=validate,
            readiness=primary.readiness if profiles else lambda:('No model weights are installed; select a model in Install.',),
            capabilities=children.get('h3-convrot-int8',primary).capabilities,
            resident=any(c.resident for c in children.values()),
            command_for_profile=lambda name:selected(name).command,backend_for_profile=selected)

    def _read(self):
        try:return read_json(self.paths,STATE)
        except FileNotFoundError:return {'state':'idle','completed':0,'total':0,'error':None}

    def _publish(self,**values):
        with self.lock:atomic_json(self.paths,STATE,{**self._read(),**values})

    def status(self):
        with self.lock:
            installed=self.worker.backend.profiles
            return {**self._read(),'supported':self.supported,'busy':bool(self.thread and self.thread.is_alive()),
                'profiles':[{'profile':name,'artifact_set_digest':get_artifact_set(name).digest,
                    'compatible':self.supported,'runtime_installed':bool(self.python_for_profile(name)) if self.supported else False,
                    'installed':name in installed,'total_bytes':get_artifact_set(name).weight_bytes}
                    for name in MODEL_NAMES]}

    def recover(self):
        import fcntl
        with self.lock:
            state=self._read();maintenance=self.worker.maintenance_status()
            if not state.get('operation') or maintenance.get('operation')!=state['operation'] or not maintenance.get('active'):return
            with regular_file(self.paths,LOCK,flags=os.O_CREAT|os.O_RDWR,mode='r+b',create_parents=True) as guard:
                try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:return
                self.worker.end_maintenance(state['operation'])
                self._publish(state='failed',error='Interrupted model installation. Apply again to verify and resume; existing models are unchanged.')

    def start(self,profile,**approval):
        selected=get_artifact_set(profile)
        if not self.supported:raise ValueError('A managed runtime must be set up first')
        if approval.get('artifact_set_digest')!=selected.digest:raise ValueError('Model selection changed')
        with self.lock:
            if self.thread and self.thread.is_alive():
                if self._read().get('profile')!=profile:raise ValueError('Another model is installing')
                return self.status()
            self.recover();operation=uuid.uuid4().hex
            self.worker.begin_maintenance(operation,policy='reject')
            try:
                record_consent(self.paths,profile_name=profile,**approval)
                self._publish(state='verifying',profile=profile,operation=operation,completed=0,total=selected.weight_bytes,error=None)
                self.stop.clear();self.thread=threading.Thread(target=self._run,args=(profile,operation),daemon=True,name='h3-add-model');self.thread.start()
            except BaseException:self.worker.end_maintenance(operation);raise
            return self.status()

    def _progress(self,event):
        now=time.monotonic()
        if event.progress and (now-self.last_progress>=.5 or event.progress.completed==event.progress.total):
            self._publish(completed=event.progress.completed,total=event.progress.total);self.last_progress=now

    def _run(self,profile,operation,*,release=True,maintenance_fd=None,artifact=None):
        import fcntl
        try:
            with regular_file(self.paths,LOCK,flags=os.O_CREAT|os.O_RDWR,mode='r+b',create_parents=True) as guard:
                fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
                fd=maintenance_fd if maintenance_fd is not None else guard.fileno()
                python=self.python_for_profile(profile)
                if python is None:
                    python=self.runtime_setup.ensure(profile,artifact,fd,self.stop,self._publish)['python']
                args=([python,'-m','h3server.backends.pruned','--profile',profile,'--environment-only'] if profile in PRUNED_PROFILE_NAMES else [python,'-m','h3server.backends.h3'])
                result=subprocess.run([*args,'--root',str(self.paths.root),'--diagnose'],env=self.worker._environment(),cwd=self.paths.root,
                    stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=180,pass_fds=(fd,))
                report=json.loads(result.stdout)
                if result.returncode or not report.get('environment_ready'):
                    issues='; '.join(str(issue) for issue in report.get('issues',[]))
                    raise ValueError('Selected model runtime/GPU verification failed; no weights downloaded. '+safe_log(issues)[:1500])
                if self.stop.is_set():raise ValueError('Model installation cancelled')
                try:verify_snapshot(self.paths,profile_name=profile,cancelled=self.stop.is_set)
                except (FileNotFoundError,ValueError):
                    if shutil.disk_usage(self.paths.root).free<get_artifact_set(profile).weight_bytes+5*1024**3:raise ValueError('Insufficient free space for private model copies and output reserve')
                    self._publish(state='downloading',completed=0,total=get_artifact_set(profile).weight_bytes)
                    download_model(self.paths,profile_name=profile,emit=self._progress,cancelled=self.stop.is_set)
                if self.stop.is_set():raise ValueError('Model installation cancelled')
                with self.worker._lock:
                    registered=self._registered(compatible_only=False);registered[profile]=get_artifact_set(profile).digest
                    try:schema=read_json(self.paths,self.registry).get('schema',1)
                    except FileNotFoundError:schema=1
                    atomic_json(self.paths,self.registry,{'schema':schema,'profiles':registered})
                    self._refresh_backend()
                    from h3server.backends.h3 import RuntimeReadiness
                    ready=self._children[profile].readiness
                    if isinstance(ready,RuntimeReadiness):
                        with ready.lock:
                            ready.report={**report,'model_ready':True};ready.checked=time.monotonic()
                self._publish(state='installed',completed=get_artifact_set(profile).weight_bytes,error=None)
        except Exception as error:
            self._publish(state='cancelled' if self.stop.is_set() else 'failed',error=safe_log(f'{type(error).__name__}: {error}')[:2000])
        finally:
            if release:self.worker.end_maintenance(operation)

    def cancel(self,operation):
        with self.lock:
            if operation!=self._read().get('operation'):raise ValueError('Model installation ownership changed')
            self.stop.set();return self.status()

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join()
