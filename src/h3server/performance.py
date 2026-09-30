"""Private, prompt-free measurements. No network/upload behavior."""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

SCHEMA = 1
STAGES = {'loading','text_encoding','denoising','video_decoding','audio_decoding','mux','export','validation'}
HARDWARE_KEYS = {'gpus','active_gpu','cpu_model','logical_cpus','ram_bytes','os','os_release','architecture','driver','torch','cuda','triton','sageattention','runtime_revision','runtime_id','environment_digest'}
GPU_KEYS = {'name','vram_bytes','compute_capability'}


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def configuration(request):
    result=asdict(request.settings)
    result['loras']=list(request.loras)
    result['prompt_length_bucket']=((len(request.prompt)+255)//256)*256
    from h3server.backends.catalog import get_artifact_set, PRUNED_PROFILE_NAMES
    if request.settings.profile in PRUNED_PROFILE_NAMES:
        result['model_artifact_digest']=get_artifact_set(request.settings.profile).digest
    return result


def expected_attention(config):
    return {'sage':config.get('sage_attention',False), 'sparse':'native_kitchen_int8' if config.get('sparse_preset','off')!='off' else 'off'}


def key_for(config,hardware,cache,actual):
    effective=dict(config)
    effective.pop('seed',None)
    if not effective.get('turbo_lora'):
        for k in ('turbo_strength','turbo_low_vram','turbo_artifact_digest'):effective.pop(k,None)
    if effective.get('sparse_preset','off')=='off':effective.pop('sparse_artifact_digest',None)
    return hashlib.sha256(encoded([SCHEMA,effective,hardware,cache,actual]).encode()).hexdigest()


class Measurements:
    def __init__(self, *, clock=time.monotonic):
        self.clock=clock;self.started=clock();self.starts={};self.stages={};self.steps=[];self.step_time=None;self.step_index=0

    def observe(self,event):
        now=self.clock()
        if event.stage not in STAGES:return
        if event.status=='running':
            self.starts.setdefault(event.stage,now)
            if event.stage=='denoising':
                if self.step_time is None:self.step_time=self.starts[event.stage]
                p=event.progress
                if p and p.unit=='steps' and p.completed==self.step_index+1:
                    self.steps.append(round(now-self.step_time,6));self.step_time=now;self.step_index=p.completed
        elif event.status=='completed' and event.stage in self.starts:
            self.stages[event.stage]=round(now-self.starts.pop(event.stage),6)

    def result(self):
        return {'total_seconds':round(self.clock()-self.started,6),'stages':self.stages,'sampling_steps':self.steps}


def _number(value):
    if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=7*86400:
        raise ValueError('invalid measurement')
    return value


def validate_measurement(metrics,hardware,actual):
    if type(metrics) is not dict or set(metrics)!={'total_seconds','stages','sampling_steps'}:raise ValueError('invalid measurement fields')
    _number(metrics['total_seconds'])
    if type(metrics['stages']) is not dict or set(metrics['stages'])-STAGES:raise ValueError('invalid stages')
    for value in metrics['stages'].values():_number(value)
    if type(metrics['sampling_steps']) is not list or len(metrics['sampling_steps'])>1000:raise ValueError('invalid sampling steps')
    for value in metrics['sampling_steps']:_number(value)
    if type(hardware) is not dict or set(hardware)-HARDWARE_KEYS:raise ValueError('private or unknown hardware fields')
    for name,value in hardware.items():
        if name=='gpus':
            if type(value) is not list or len(value)>64:raise ValueError('invalid GPU inventory')
        elif name in {'active_gpu','logical_cpus','ram_bytes'}:
            if value is not None and (type(value) is not int or not 0<=value<2**64):raise ValueError('invalid hardware number')
        elif value is not None and (type(value) is not str or len(value)>512):raise ValueError('invalid hardware text')
    for gpu in hardware.get('gpus',[]):
        if type(gpu) is not dict or set(gpu)-GPU_KEYS:raise ValueError('private or unknown GPU fields')
        if not isinstance(gpu.get('name'),str) or len(gpu['name'])>200:raise ValueError('invalid GPU name')
        if type(gpu.get('vram_bytes')) is not int or not 0<gpu['vram_bytes']<2**64:raise ValueError('invalid GPU memory')
        cc=gpu.get('compute_capability')
        if type(cc) is not list or len(cc)!=2 or any(type(n) is not int or not 0<=n<=1000 for n in cc):raise ValueError('invalid GPU capability')
    if type(actual) is not dict or set(actual)!={'sage','sparse'} or type(actual['sage']) is not bool or actual['sparse'] not in {'off','native_kitchen_int8','unconfirmed'}:raise ValueError('invalid actual attention')
    if len(encoded([metrics,hardware,actual]))>100000:raise ValueError('measurement too large')


def _update(row,value):
    n=row.get('samples',0)+1;old=row.get('mean_seconds',0);mean=old+(value-old)/n
    row.update(samples=n,mean_seconds=mean,m2=row.get('m2',0)+(value-old)*(value-mean),
               minimum_seconds=min(value,row.get('minimum_seconds',value)),maximum_seconds=max(value,row.get('maximum_seconds',value)))


def _public(row):
    return {k:v for k,v in row.items() if k!='m2'} | {'stdev_seconds':math.sqrt(max(0,row['m2']/(row['samples']-1))) if row['samples']>1 else None}


class PerformanceStore:
    def __init__(self,path,*,root=None):
        self.path=Path(path);self.root=Path(root) if root is not None else None
        if self.root is not None and self.path.parent!=self.root/'state':raise ValueError('invalid performance location')
        with self.connect() as db:
            if db.execute('PRAGMA user_version').fetchone()[0] not in (0,SCHEMA):raise ValueError('unsupported performance schema')
            db.execute('CREATE TABLE IF NOT EXISTS samples(job_id TEXT PRIMARY KEY, group_key TEXT NOT NULL, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS averages(group_key TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('PRAGMA user_version=1')

    @contextmanager
    def connect(self):
        # Parent is the already-private application state directory. Keep a no-follow
        # descriptor open while SQLite uses its anchored directory on Linux.
        flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
        directory=os.open(self.root if self.root is not None else self.path.parent,flags)
        if self.root is not None:
            try:child=os.open('state',flags,dir_fd=directory)
            finally:os.close(directory)
            directory=child
        fd=None;db=None
        try:
            fd=os.open(self.path.name,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=directory)
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_nlink!=1:raise ValueError('unsafe performance database')
            root=Path('/proc/self/fd')/str(directory) if Path('/proc/self/fd').exists() else self.path.parent
            db=sqlite3.connect(root/self.path.name,timeout=10)
            with db:yield db
        finally:
            if db is not None:db.close()
            if fd is not None:os.close(fd)
            os.close(directory)

    def record(self,job_id,config,hardware,metrics,cache,actual):
        if not re.fullmatch('[0-9a-f]{32}',job_id) or cache not in {'cold','warm','warm_partial','encoder_reuse'}:raise ValueError('invalid performance identity')
        from dataclasses import fields
        from h3server.domain import GenerationSettings
        allowed={f.name for f in fields(GenerationSettings)}|{'loras','prompt_length_bucket','model_artifact_digest'}
        if type(config) is not dict or set(config)-allowed:raise ValueError('unknown/private configuration fields')
        validate_measurement(metrics,hardware,actual)
        group=key_for(config,hardware,cache,actual)
        data={'schema_version':SCHEMA,'configuration':config,'hardware':hardware,'cache':cache,'actual_attention':actual,'measurements':metrics,'recorded_at':time.time()}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('INSERT OR IGNORE INTO samples VALUES(?,?,?)',(job_id,group,encoded(data))).rowcount:return
            prior=db.execute('SELECT payload FROM averages WHERE group_key=?',(group,)).fetchone()
            avg=json.loads(prior[0]) if prior else {'total':{},'stages':{},'sampling_steps':[]}
            _update(avg['total'],metrics['total_seconds'])
            for name,value in metrics['stages'].items():_update(avg['stages'].setdefault(name,{}),value)
            for i,value in enumerate(metrics['sampling_steps']):
                if i==len(avg['sampling_steps']):avg['sampling_steps'].append({})
                _update(avg['sampling_steps'][i],value)
            db.execute('INSERT OR REPLACE INTO averages VALUES(?,?)',(group,encoded(avg)))

    def estimate(self,config,hardware,cache):
        with self.connect() as db:
            row=db.execute('SELECT payload FROM averages WHERE group_key=?',(key_for(config,hardware,cache,expected_attention(config)),)).fetchone()
        if row is None:return {'samples':0,'cache':cache,'reason':'No matching measured hardware/configuration/cache samples yet.'}
        avg=json.loads(row[0])
        return {**_public(avg['total']),'cache':cache,'stages':{k:_public(v) for k,v in avg['stages'].items()},'sampling_steps':[_public(v) for v in avg['sampling_steps']],
                'basis':'Measured generation time; queue delay excluded. Observed spread is not a guaranteed bound.'}

    def export(self,offset=0,limit=50):
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=50:raise ValueError('invalid performance page')
        with self.connect() as db:rows=db.execute('SELECT payload FROM samples ORDER BY rowid DESC LIMIT ? OFFSET ?',(limit,offset)).fetchall()
        return {'schema_version':SCHEMA,'records':[json.loads(row[0]) for row in rows],'next_offset':offset+len(rows) if len(rows)==limit else None,'uploaded':False}
