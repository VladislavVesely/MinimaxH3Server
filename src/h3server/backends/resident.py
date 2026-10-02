"""Owned, sequential inference session. Private pipes only; no listening socket."""
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import asdict
import gc
import hashlib
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import threading

from h3server.backends.base import regular_file
from h3server.domain import Event, StageStatus
from h3server.performance import Measurements


class ComponentCache:
    def __init__(self):
        self.runtime=None;self.components={};self.prepared=None;self.variant=None
        self.assets=None;self.hardware=None;self.was_warm=False;self.reloaded_for_memory=False;self.reloaded_base=False

    def release_diffusion(self,rt):
        for model in (self.prepared,getattr(self,'retiring',None)):
            if model is not None:model.unpatch_model(unpatch_weights=False)
        model=None
        self.prepared=None;self.retiring=None;self.variant=None
        self.components.pop('diffusion_model',None)
        gc.collect();rt.mm.cleanup_models();rt.mm.soft_empty_cache()
        self.reloaded_base=True

    def prepare_encoding(self,rt,emit,*,rebuild_turbo=False):
        """Retain the encoder; evict decoders, then diffusion only if RAM requires it.
        Call before taking local references to cached diffusion/decoder objects.
        """
        if not self.was_warm or not hasattr(rt.mm,'get_free_memory'): return
        clip=self.components.get('text_encoder')
        if clip is None: return
        if rebuild_turbo and 'diffusion_model' in self.components:
            # A new Turbo variant merges FC2 weights. Reusing anonymously backed
            # offloaded weights can exhaust RAM when the encoder is offloaded
            # during sampler loading. A fresh mmap-backed base is reclaimable.
            reserve=self.components['diffusion_model'].model_size()+clip.patcher.model_size()+4*1024**3
            if rt.mm.psutil.virtual_memory().available<reserve:
                self.release_diffusion(rt)
                emit(Event('loading',StageStatus.RUNNING,'Preparing a changed Turbo variant within host RAM: text encoder retained; base weights reload from their reclaimable disk mapping.'))
        needed=clip.patcher.model_size()*1.15 + 2*1024**3
        free_gpu=rt.mm.get_free_memory(rt.mm.get_torch_device())
        available=rt.mm.psutil.virtual_memory().available
        # Do not plan to consume the machine's remaining swap for model retention.
        budget=max(0,available-3*1024**3)
        if max(0,needed-free_gpu)>budget:
            removed=[]
            for name in ('video_vae','audio_vae'):
                if name in self.components:
                    del self.components[name];removed.append(name)
            if removed:
                gc.collect();rt.mm.cleanup_models();rt.mm.soft_empty_cache()
                self.reloaded_for_memory=True
                emit(Event('loading',StageStatus.RUNNING,'System memory pressure: released decoder caches before text encoding; base model and text encoder retained. Decoders reload only when needed.'))
            remaining=max(0,needed-rt.mm.get_free_memory(rt.mm.get_torch_device()))
            safe_host=rt.mm.psutil.virtual_memory().available-1024**3+min(rt.mm.psutil.swap_memory().free,2*1024**3)
            if remaining>max(0,safe_host):
                # Preserve the encoder rather than failing or pushing the host into
                # OOM. No caller holds a local model reference at this boundary.
                self.release_diffusion(rt)
                emit(Event('loading',StageStatus.RUNNING,'System memory pressure: keeping the text encoder; diffusion and decoder weights will reload only when needed.'))
                remaining=max(0,needed-rt.mm.get_free_memory(rt.mm.get_torch_device()))
                safe_host=rt.mm.psutil.virtual_memory().available-1024**3+min(rt.mm.psutil.swap_memory().free,2*1024**3)
                if remaining>max(0,safe_host):
                    raise MemoryError('Insufficient memory even after releasing caches; free system/GPU memory before retrying.')

    def signatures(self,paths,files):
        result={}
        for path in files:
            relative=Path(path).relative_to(paths.root).as_posix()
            with regular_file(paths,relative) as f:
                s=os.fstat(f.fileno());result[relative]=(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        return result

    def capture(self,paths,files):
        self.assets=self.signatures(paths,files)

    def verify(self,paths):
        if self.signatures(paths,[paths.root/p for p in self.assets])!=self.assets:
            raise ValueError('Retained model files changed; cached models cannot be reused')


def hardware(rt,runtime_id):
    torch=rt.torch
    cpu='unknown'
    try:
        for line in Path('/proc/cpuinfo').read_text().splitlines():
            if line.startswith('model name'):
                cpu=line.split(':',1)[1].strip()[:200];break
    except OSError:pass
    def version(name):
        try:return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:return None
    try:ram=os.sysconf('SC_PHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')
    except (ValueError,OSError):ram=None
    driver=None
    try:
        p=subprocess.run(['nvidia-smi','--query-gpu=driver_version','--format=csv,noheader'],capture_output=True,timeout=5,check=True)
        driver=p.stdout.decode().splitlines()[0].strip()[:64]
    except (OSError,subprocess.SubprocessError,IndexError,UnicodeError):pass
    revision=hashlib.sha256()
    for name in ('resident.py','pruned.py','h3.py','sparse.py','turbo.py','conditioning.py','reference.py','../assets.py','../performance.py','../runtime_memory.py'):
        revision.update((Path(__file__).parent/name).read_bytes())
    from h3server.runtime_memory import encoded, startup_settings
    revision.update(encoded(startup_settings()).encode())
    return {'gpus':[{'name':torch.cuda.get_device_name(i),'vram_bytes':torch.cuda.get_device_properties(i).total_memory,
                     'compute_capability':list(torch.cuda.get_device_capability(i))} for i in range(torch.cuda.device_count())],
            'active_gpu':torch.cuda.current_device(),'cpu_model':cpu,'logical_cpus':os.cpu_count(),'ram_bytes':ram,
            'os':platform.system(),'os_release':platform.release(),'architecture':platform.machine(),'driver':driver,
            'torch':torch.__version__,'cuda':torch.version.cuda,'triton':version('triton'),'sageattention':version('sageattention'),
            'runtime_revision':revision.hexdigest(),'runtime_id':runtime_id,'environment_digest':getattr(rt,'environment_digest',None)}


def serve(kind,profile,stdout):
    cache=ComponentCache()
    destination=[None];current_stage=['loading'];lock=threading.RLock()
    def send(value):
        with lock:
            stdout.write(json.dumps(value,separators=(',',':'),allow_nan=False)+'\n');stdout.flush()
    class Log:
        def __init__(self):self.pending='';self.discarding=False
        def write(self,text):
            for part in text.splitlines(keepends=True):
                if not self.discarding:
                    self.pending+=part
                    if len(self.pending)>4000:self.pending='';self.discarding=True
                if part.endswith(('\n','\r')):
                    if destination[0] and self.pending.strip():destination[0](Event(current_stage[0],StageStatus.RUNNING,self.pending.rstrip()))
                    self.pending='';self.discarding=False
            return len(text)
        def flush(self):pass
        def isatty(self):return False
    sink=Log()
    with redirect_stdout(sink),redirect_stderr(sink):
        logging.basicConfig(level=logging.INFO,stream=sink,format='%(message)s')
        while True:
            line=sys.stdin.buffer.readline(8193)
            if not line:return
            if len(line)>8192 or not line.endswith(b'\n'):raise ValueError('resident command size')
            command=json.loads(line)
            if type(command) is not dict or set(command)!={'job_id','request_path'} or not re.fullmatch('[a-f0-9]{32}',command['job_id']):raise ValueError('invalid resident command')
            job_id=command['job_id'];path=Path(command['request_path'])
            if path.parent.name!=job_id:raise ValueError('resident command binding mismatch')
            measurement=Measurements()
            def emit(event):
                current_stage[0]=event.stage
                measurement.observe(event)
                send({'type':'event','job_id':job_id,'event':asdict(event)})
            destination[0]=emit;current_stage[0]='loading';cache.was_warm=bool(cache.components);cache.reloaded_for_memory=False;cache.reloaded_base=False
            try:
                if kind=='pruned':
                    from h3server.backends.pruned import execute_document
                    execute_document(path,emit=emit,profile=profile,cache=cache)
                else:
                    raise ValueError('Resident reuse is not verified for this adapter')
                metrics=measurement.result()
                from h3server.backends.catalog import get_artifact_set
                cache.hardware=hardware(cache.runtime,get_artifact_set(profile).runtime_id)
                actual=getattr(cache.runtime,'actual_attention',{'sage':False,'sparse':'off'})
                destination[0]=None;sink.pending=''
                gc.collect()  # Drop per-job latents, not retained models or allocator caches.
                send({'type':'result','job_id':job_id,'ok':True,'components':list(cache.components),
                      'measurement':{'hardware':cache.hardware,'metrics':metrics,'cache':('encoder_reuse' if cache.reloaded_base else 'warm_partial' if cache.reloaded_for_memory else 'warm') if cache.was_warm else 'cold','actual_attention':actual}})
            except BaseException as error:
                from h3server.installer.diagnostics import safe_log
                emit(Event(current_stage[0],StageStatus.FAILED,safe_log(f'{type(error).__name__}: {error}')[:4000]))
                destination[0]=None
                send({'type':'result','job_id':job_id,'ok':False,'components':[],'measurement':None})
                return  # Never reuse potentially corrupted native state after an exception.
