"""Explicit Ref2VA acquisition with durable state and idle maintenance ownership."""
import os
import threading
import time
import uuid
from h3server.backends import reference
from h3server.backends.base import atomic_json,read_json,regular_file
from h3server.installer.diagnostics import safe_log

STATE='state/reference-install.json'
LOCK='state/reference-install.lock'

class ReferenceInstaller:
    def __init__(self,worker):
        self.worker=worker;self.paths=worker.paths;self.lock=threading.RLock();self.thread=None;self.stop=threading.Event();self.last_progress=0

    def _read(self):
        try:return read_json(self.paths,STATE)
        except FileNotFoundError:return dict(state='not_installed',completed=0,total=reference.TOTAL,error=None)

    def _publish(self,**values):
        with self.lock:atomic_json(self.paths,STATE,{**self._read(),**values})

    def status(self):
        with self.lock:
            installed=reference.installed(self.paths)
            return {**self._read(),'installed':installed,'busy':bool(self.thread and self.thread.is_alive()),
                    'supported':bool(self.worker.backend.profiles) and any(p in reference.SUPPORTED_PROFILES for p in self.worker.backend.profiles),
                    'artifact_digest':reference.DIGEST,'total':reference.TOTAL,'checkpoint':reference.WEIGHT,'license':reference.BINDING['license']}

    def recover(self):
        import fcntl
        with self.lock:
            state=self._read();maintenance=self.worker.maintenance_status()
            if not state.get('operation') or maintenance.get('operation')!=state['operation'] or not maintenance.get('active'):return
            with regular_file(self.paths,LOCK,flags=os.O_CREAT|os.O_RDWR,mode='r+b',create_parents=True) as guard:
                try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:return
                self.worker.end_maintenance(state['operation'])
                if state.get('state')=='installing':self._publish(state='failed',error='Interrupted Ref2VA download; retry explicitly to resume.')

    def start(self,digest):
        if digest!=reference.DIGEST:raise ValueError('Ref2VA selection changed')
        with self.lock:
            if self.thread and self.thread.is_alive():return self.status()
            self.recover()
            if not self.status()['supported']:raise ValueError('Ref2VA requires ConvRot INT8')
            if reference.installed(self.paths,verify=True):return self.status()
            from h3server.downloads import require_consent
            require_consent(self.paths,profile_name=self.worker.backend.profiles[0])
            operation=uuid.uuid4().hex;self.worker.begin_maintenance(operation,policy='reject')
            try:
                atomic_json(self.paths,reference.CONSENT,reference.BINDING)
                self._publish(state='installing',operation=operation,error=None)
                self.stop.clear();self.thread=threading.Thread(target=self._run,args=(operation,),daemon=True,name='h3-reference-download');self.thread.start()
            except BaseException:self.worker.end_maintenance(operation);raise
            return self.status()

    def progress(self,c,t):
        now=time.monotonic()
        if now-self.last_progress>=.5 or c==t:self._publish(completed=c,total=t);self.last_progress=now

    def _run(self,operation):
        import fcntl
        try:
            with regular_file(self.paths,LOCK,flags=os.O_CREAT|os.O_RDWR,mode='r+b',create_parents=True) as guard:
                fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
                reference.download(self.paths,progress=self.progress,cancelled=self.stop.is_set)
                self._publish(state='installed',completed=reference.TOTAL,error=None)
        except Exception as e:self._publish(state='failed',error=safe_log(f'{type(e).__name__}: {e}')[:2000])
        finally:self.worker.end_maintenance(operation)

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join()
