"""End-to-end authenticated reconciliation and fixture execution, no real GPU/download."""
from dataclasses import replace
import hashlib
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
from h3server.backends import load_manifest
from h3server.backends.base import atomic_json
from h3server.backends.comfy_runtime import runtime_record
from h3server.installer.planner import Inspection
from h3server.domain import GenerationSettings, JobRequest, JobStatus
from test_components import components, A
from test_remote_api import HEADERS
from test_worker import wait_status

_REAL_RUN=subprocess.run
Q5='h3-gguf-q5-k-m'


def test_api_provisions_separate_runtime_then_downloads_and_executes_it(components,monkeypatch):
    f=components;calls=[]
    import h3server.remote.runtime_setup as setup
    import h3server.backends.pruned as pruned
    monkeypatch.setattr(subprocess,'run',_REAL_RUN)
    monkeypatch.setattr(setup,'inspect_prerequisites',lambda:Inspection('ubuntu','26.04','x86_64',True,True,True,'disabled',False,True,None,(),(),()))
    payload=b'fixture-wheel';digest=hashlib.sha256(payload).hexdigest()
    artifact=dict(release_id='fixture',filename='h3server-0.1-py3-none-any.whl',sha256=digest)
    wheel=f.root/'releases/fixture'/artifact['filename'];wheel.parent.mkdir(parents=True);wheel.write_bytes(payload)
    class Steps:
        def __init__(self,paths,plan,digest,io,log):
            self.paths=paths;self.plan=plan;self.directory=paths.environments/digest
            assert os.fstat(io.lock_fd).st_uid==os.getuid()
        def environment(self):
            calls.append('environment');(self.directory/'bin').mkdir(exist_ok=True)
            python=self.directory/'bin/python'
            python.write_text('#!'+sys.executable+'\n'+'''import json, pathlib, sys
prefix=str(pathlib.Path(__file__).parent.parent)
if '--resident' in sys.argv:
    for line in sys.stdin:
        msg=json.loads(line);data=json.loads(pathlib.Path(msg['request_path']).read_text())
        assert data['request']['settings']['profile']=='h3-gguf-q5-k-m'
        pathlib.Path(data['output_path']).write_bytes(b'fixture-runtime-output')
        print(json.dumps(dict(type='result',job_id=msg['job_id'],ok=True,components=[],measurement=None)),flush=True)
else:
    print(json.dumps(dict(environment_ready=True,environment=prefix,model_ready=False,issues=[])),flush=True)
''');python.chmod(0o700)
            atomic_json(self.paths,'config/installation-'+self.directory.name+'.json',dict(profile=self.plan['profile'],environment=str(self.directory),artifact=self.plan['artifact']))
        def dependencies(self):
            calls.append('dependencies');frozen='torch==fixture\n';(self.directory/'resolved.txt').write_text(frozen)
            atomic_json(self.paths,'environments/'+self.directory.name+'/runtime.json',runtime_record(Q5,frozen))
        def verify(self):
            calls.append('verify');atomic_json(self.paths,'environments/'+self.directory.name+'/readiness.json',{'environment_ready':True})
    monkeypatch.setattr(setup,'Steps',Steps)
    monkeypatch.setattr(pruned,'backend',lambda paths,name,**kw:replace(f.sync.library.base,profiles=(name,),
        command=(kw.get('python',sys.executable),),validate_video=lambda fd:os.read(fd,128)==b'fixture-runtime-output'))
    with TestClient(f.app) as client:
        inventory=client.get('/api/v1/models/library',headers=HEADERS).json()['components']
        response=client.post('/api/v1/models/install',headers=HEADERS,json=dict(action='synchronize',desired=[A,Q5],
            inventory_digest=inventory['digest'],runtime_artifact=artifact,license_sha256=load_manifest()['sources']['license']['sha256'],
            license_acknowledged=True,eligibility_confirmed=True,download_approved=True))
        assert response.status_code==202,response.text
        f.sync.thread.join(10)
        state=f.sync.status();assert state['state']=='completed',state
        assert calls==['environment','dependencies','verify']
        bound=f.sync.library.runtimes.resolve(Q5)
        assert bound['python']!=sys.executable
        assert f.worker.selected_backend(Q5).command[0]==bound['python']
        assert (f.root/next(iter(f.files(A)))).read_bytes()==b'abc'
        job=f.worker.submit(JobRequest('fixture only',settings=GenerationSettings(profile=Q5)))
        assert wait_status(f.store,job.job_id,{JobStatus.COMPLETED,JobStatus.FAILED}).status is JobStatus.COMPLETED
