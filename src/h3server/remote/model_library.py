"""Explicit additional-weight installation in an unchanged verified runtime."""
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
from h3server.backends.catalog import PRUNED_PROFILE_NAMES, get_artifact_set
from h3server.backends.comfy_runtime import compatible_runtime
from h3server.downloads import record_consent, require_consent, download_model, verify_snapshot
from h3server.installer.diagnostics import safe_log

STATE='state/model-library-install.json'
LOCK='state/model-library-install.lock'


class ModelLibrary:
    def __init__(self, worker):
        self.worker=worker; self.paths=worker.paths; self.base=worker.backend
        self.profile=next(iter(self.base.profiles),''); self.environment=Path(sys.prefix).name
        self.registry='config/model-library.json'
        self.lock=threading.RLock();self.thread=None;self.stop=threading.Event();self.last_progress=0
        self.supported=self.profile in PRUNED_PROFILE_NAMES and self.base.resident
        if self.supported:self._refresh_backend()

    def _registered(self,compatible_only=True):
        try:data=read_json(self.paths,self.registry)
        except FileNotFoundError:return {}
        if data.get('schema')!=1 or not isinstance(data.get('profiles'),dict):raise ValueError('invalid model library binding')
        for name,digest in data['profiles'].items():
            if digest!=get_artifact_set(name).digest:raise ValueError('model library selection changed')
            require_consent(self.paths,profile_name=name)
        return {name:digest for name,digest in data['profiles'].items() if not compatible_only or compatible_runtime(self.profile,name)}

    def _refresh_backend(self):
        from h3server.backends.pruned import backend
        profiles=tuple(dict.fromkeys([self.profile,*self._registered()]))
        children={name:(self.base if name==self.profile else backend(self.paths,name)) for name in profiles}
        def selected(name):
            if name not in children:raise ValueError('model is not installed in this runtime')
            return children[name]
        def validate(request):selected(request.settings.profile).validate_request(request)
        # Each resident child remains bound to one profile. The scheduler already
        # recycles it on profile changes; no weights are silently mixed.
        self.worker.backend=replace(self.base,profiles=profiles,validate_request=validate,
            capabilities=children.get('h3-convrot-int8',self.base).capabilities,
            command_for_profile=lambda name:selected(name).command)

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
                    'compatible':self.supported and compatible_runtime(self.profile,name),
                    'installed':name in installed,'total_bytes':get_artifact_set(name).weight_bytes}
                    for name in PRUNED_PROFILE_NAMES]}

    def recover(self):
        import fcntl
        with self.lock:
            state=self._read();maintenance=self.worker.maintenance_status()
            if not state.get('operation') or maintenance.get('operation')!=state['operation'] or not maintenance.get('active'):return
            with regular_file(self.paths,LOCK,flags=os.O_CREAT|os.O_RDWR,mode='r+b',create_parents=True) as guard:
                try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:return
                self.worker.end_maintenance(state['operation'])
                self._publish(state='failed',error='Interrupted model installation. Click Install to verify and resume; existing models are unchanged.')

    def start(self,profile,**approval):
        selected=get_artifact_set(profile)
        if not self.supported or not compatible_runtime(self.profile,profile):raise ValueError('Selected model requires a different runtime setup')
        if approval.get('artifact_set_digest')!=selected.digest:raise ValueError('Model selection changed')
        with self.lock:
            if self.thread and self.thread.is_alive():
                if self._read().get('profile')!=profile:raise ValueError('Another model is downloading')
                return self.status()
            self.recover()
            operation=uuid.uuid4().hex
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

    def _run(self,profile,operation):
        import fcntl
        try:
            with regular_file(self.paths,LOCK,flags=os.O_CREAT|os.O_RDWR,mode='r+b',create_parents=True) as guard:
                fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
                # Fixed diagnostic only: no package installation or model loading.
                result=subprocess.run([sys.executable,'-m','h3server.backends.pruned','--profile',profile,'--root',str(self.paths.root),'--diagnose','--environment-only'],
                    env=self.worker._environment(),cwd=self.paths.root,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=180)
                report=json.loads(result.stdout)
                if result.returncode or not report.get('environment_ready'):
                    issues='; '.join(str(issue) for issue in report.get('issues',[]))
                    raise ValueError('Selected model runtime/GPU verification failed; no weights downloaded. '+safe_log(issues)[:1500])
                if self.stop.is_set():raise ValueError('Model installation cancelled')
                try:verify_snapshot(self.paths,profile_name=profile,cancelled=self.stop.is_set)
                except (FileNotFoundError,ValueError):
                    if shutil.disk_usage(self.paths.root).free<get_artifact_set(profile).weight_bytes+5*1024**3:raise ValueError('Insufficient free space for private model copies and output reserve')
                    self._publish(state='downloading')
                    download_model(self.paths,profile_name=profile,emit=self._progress,cancelled=self.stop.is_set)
                if self.stop.is_set():raise ValueError('Model installation cancelled')
                with self.worker._lock:
                    registered=self._registered(compatible_only=False);registered[profile]=get_artifact_set(profile).digest
                    atomic_json(self.paths,self.registry,{'schema':1,'profiles':registered})
                    self._refresh_backend()
                self._publish(state='installed',completed=get_artifact_set(profile).weight_bytes,error=None)
        except Exception as error:
            self._publish(state='cancelled' if self.stop.is_set() else 'failed',error=safe_log(f'{type(error).__name__}: {error}')[:2000])
        finally:self.worker.end_maintenance(operation)

    def cancel(self,operation):
        with self.lock:
            if operation!=self._read().get('operation'):raise ValueError('Model installation ownership changed')
            self.stop.set();return self.status()

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join()
