"""Fixed read-only target observer, shipped inside the controller (no daemon update).

Only selected version/revision fields leave the target. Never import target packages,
read pip URLs/authentication files, install anything, or initialize CUDA.
"""
import json
import shlex

SOURCE = r'''
import os, sys, stat, json, re, subprocess, time, platform, datetime, select, signal
root=sys.argv[1]; started=time.monotonic()
result={'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(), 'environments':[], 'system_packages':[], 'components':[], 'tools':[], 'issues':[]}
def issue(text):
    if text not in result['issues']:result['issues'].append(text)
def clean(value):
    return value if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.+!~:/@ -]{1,160}',value) else 'Unknown'
def run(args):
    if time.monotonic()-started>18:issue('Observation time budget reached; inventory is partial.');return ''
    p=None
    try:
        p=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True,env={**os.environ,'LC_ALL':'C'})
        deadline=time.monotonic()+2;output=bytearray()
        while time.monotonic()<deadline:
            if select.select([p.stdout],[],[],.05)[0]:
                part=os.read(p.stdout.fileno(),32768)
                if not part:
                    p.wait(timeout=max(.01,deadline-time.monotonic()))
                    return output.decode('utf-8','replace') if p.returncode==0 else ''
                output.extend(part)
                if len(output)>512000:issue('A command exceeded the output limit; inventory is partial.');return ''
        return ''
    except (OSError,subprocess.TimeoutExpired):return ''
    finally:
        if p is not None:
            if p.poll() is None:
                try:os.killpg(p.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                p.wait()
            p.stdout.close()
rootfd=None
def opened(rel,directory=False,metadata=False):
    if rootfd is None:raise FileNotFoundError()
    fd=os.dup(rootfd)
    try:
        parts=rel.split('/')
        for i,part in enumerate(parts):
            if part in ('','..','.'):raise ValueError('invalid relative path')
            child=os.open(part,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|(os.O_DIRECTORY if i<len(parts)-1 or directory else 0),dir_fd=fd)
            os.close(fd);fd=child;s=os.fstat(fd)
            if s.st_uid!=os.getuid() or (s.st_mode&0o022 and not metadata):raise ValueError('unowned or writable entry')
        return fd
    except Exception:os.close(fd);raise

def read(rel,limit=2000000,prefix=False,metadata=False):
    fd=opened(rel,metadata=metadata)
    try:
        s=os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or (s.st_size>limit and not prefix):raise ValueError('invalid file')
        return os.read(fd,limit).decode('utf-8','replace')
    finally:os.close(fd)
def names(rel):
    fd=opened(rel,True)
    try:
        values=os.listdir(fd)
        if len(values)>6000:raise ValueError('directory limit')
        return sorted(values)
    finally:os.close(fd)
def document(rel):return json.loads(read(rel))
try:
    rootfd=os.open(root,os.O_RDONLY|os.O_NOFOLLOW|os.O_DIRECTORY)
    s=os.fstat(rootfd)
    if s.st_uid!=os.getuid() or s.st_mode&0o022:raise ValueError('unsafe root')
except FileNotFoundError:rootfd=None
except (OSError,ValueError):
    if rootfd is not None:os.close(rootfd)
    rootfd=None;issue('Managed installation directory could not be read safely.')
try:records=[x for x in names('config') if re.fullmatch(r'installation-[a-f0-9]{64}\.json',x)]
except FileNotFoundError:records=[]
except (OSError,ValueError):records=[];issue('Managed installation records could not be read safely.')
if len(records)>8:issue('More than eight environments; inventory is partial.')
for filename in records[:8]:
    try:
        record=document('config/'+filename);identifier=filename[13:-5];base='environments/'+identifier
        if record.get('environment')!=root+'/'+base:raise ValueError('environment mismatch')
        env={'id':identifier,'profile':clean(record.get('profile')),'python':'Unknown','python_recorded':'Unknown','packages':[],'sources':[],'cuda_built':'Unknown'}
        names(base)
        result['environments'].append(env)
        try:
            cfg=read(base+'/pyvenv.cfg',8192)
            version=re.search(r'^version\s*=\s*([^\n]+)',cfg,re.M)
            if version:env['python_recorded']=clean(version[1].strip())
            names(base+'/bin')
            observed=run([root+'/'+base+'/bin/python','-I','-S','-B','-c','import sys; print(".".join(map(str,sys.version_info[:3])))']).strip()
            if re.fullmatch(r'\d+\.\d+\.\d+',observed):env['python']=observed
        except (OSError,ValueError):pass
        for lib in names(base+'/lib'):
            if not re.fullmatch(r'python[0-9]+\.[0-9]+',lib):continue
            site=base+'/lib/'+lib+'/site-packages'
            for entry in names(site):
                if not re.fullmatch(r'[A-Za-z0-9_.+!-]+\.dist-info',entry):continue
                try:
                    text=read(site+'/'+entry+'/METADATA',8192,True,metadata=True).replace('\r\n','\n').split('\n\n',1)[0]
                    name=re.search(r'^Name: ([A-Za-z0-9_.-]{1,160})$',text,re.M)
                    version=re.search(r'^Version: ([A-Za-z0-9_.+!~-]{1,160})$',text,re.M)
                    if not name or not version:raise ValueError('metadata missing')
                    env['packages'].append({'name':name[1],'version':version[1]})
                    if len(env['packages'])>=512:issue('Package count limit reached; inventory is partial.');break
                except (OSError,ValueError):issue('Some installed package metadata could not be read safely.')
            try:
                text=read(site+'/torch/version.py',8192)
                match=re.search(r'^cuda(?:\s*:\s*[^=\n]+)?\s*=\s*[\x27\x22]([0-9.]+)[\x27\x22]',text,re.M)
                if match:env['cuda_built']=match[1]
            except (OSError,ValueError):pass
        env['packages'].sort(key=lambda p:p['name'].lower())
        try:
            runtime=document(base+'/runtime.json')
            pin=runtime.get('artifact_set_digest')
            if isinstance(pin,str) and re.fullmatch(r'[a-f0-9]{64}',pin):env['model_pin']=pin
            for source in runtime.get('runtime',{}).get('sources',[])[:16]:
                directory=source.get('directory','')
                if not re.fullmatch(r'[A-Za-z0-9_./-]{1,160}',directory) or '..' in directory.split('/'):continue
                try:
                    revision=read(base+'/'+directory+'/.git/HEAD',256).strip()
                    if not re.fullmatch(r'[a-f0-9]{40,64}',revision):revision='Unknown (not detached)'
                except (OSError,ValueError):revision='Unknown'
                env['sources'].append({'name':clean(source.get('repository')),'version':revision,'pinned':clean(source.get('revision'))})
        except FileNotFoundError:pass
    except (OSError,ValueError,TypeError,AttributeError):issue('An environment could only be partially observed.')
try:
    registry=document('config/model-library.json')
    for name,digest in list(registry.get('profiles',{}).items())[:32]:
        if re.fullmatch(r'[A-Za-z0-9_-]{1,80}',name) and isinstance(digest,str) and re.fullmatch(r'[a-f0-9]{64}',digest):
            result['components'].append({'name':name,'version':digest,'evidence':'Registered artifact digest; weight files not reverified'})
except FileNotFoundError:pass
except (OSError,ValueError,TypeError,AttributeError):issue('Model registration could not be read safely.')
for folder in ['state']+['state/runtime-components/'+e['id'] for e in result['environments']]:
    try:
        for name in names(folder):
            if not re.fullmatch(r'(?:sageattention|turbo|sparse|reference)[A-Za-z0-9_-]*receipt\.json',name):continue
            try:
                receipt=document(folder+'/'+name)
                binding=receipt.get('runtime',receipt)
                for key in ('version','artifact_digest','digest','revision','wheel_sha256'):
                    value=binding.get(key)
                    if isinstance(value,str) and re.fullmatch(r'(?:[a-f0-9]{40,64}|[0-9]+(?:\.[0-9]+){1,3})',value):
                        label=name[:-5]+(' / '+folder.rsplit('/',1)[-1][:12] if folder!='state' else '')
                        result['components'].append({'name':label,'version':value,'evidence':'Recorded receipt; not reverified'});break
            except (OSError,ValueError,TypeError,AttributeError):issue('A component receipt could not be read safely.')
    except FileNotFoundError:pass
    except (OSError,ValueError):issue('Component receipts could not be read safely.')
if rootfd is not None:os.close(rootfd)
result['tools'].append({'name':'System Python (observer)','version':platform.python_version()})
for name,argv,pattern in [
 ('NVIDIA driver',['nvidia-smi','--query-gpu=driver_version','--format=csv,noheader'],r'([0-9]+\.[0-9.]+)'),
 ('CUDA toolkit (nvcc)',['nvcc','--version'],r'release ([0-9.]+)'),
 ('FFmpeg',['ffmpeg','-version'],r'ffmpeg version ([A-Za-z0-9_.+~-]+)'),
 ('GCC',['gcc','-dumpfullversion'],r'([0-9.]+)'),
 ('Git',['git','--version'],r'git version ([0-9.]+)')]:
    match=re.search(pattern,run(argv))
    result['tools'].append({'name':name,'version':match[1] if match else 'Unknown / unavailable'})
rows=run(['dpkg-query','-W','-f=${binary:Package}\t${Version}\t${db:Status-Status}\n'])
if not rows:issue('System package inventory unavailable (dpkg required); tool versions may still be observed.')
for row in rows.splitlines()[:4096]:
    parts=row.split('\t')
    if len(parts)==3 and parts[2]=='installed' and all(re.fullmatch(r'[A-Za-z0-9_.+!~:-]{1,160}',x) for x in parts[:2]):
        result['system_packages'].append({'name':parts[0],'version':parts[1]})
if len(rows.splitlines())>4096:issue('System package count limit reached; inventory is partial.')
print(json.dumps(result,separators=(',',':')))
'''


def observe(connection, home):
    """Caller holds connection.lock; only the fixed script and target home are sent."""
    response = connection._execute('LC_ALL=C '+shlex.join(('python3','-I','-S','-B','-c',SOURCE,home+'/MinimaxH3Server')), 25)
    if not response.succeeded:
        return {'issues':['Installed software observation unavailable; no changes made.'], 'environments':[], 'tools':[], 'components':[], 'system_packages':[]}
    try:
        value=json.loads(response.stdout)
        if not isinstance(value,dict) or any(not isinstance(value.get(k),list) for k in ('issues','environments','tools','components','system_packages')):raise ValueError()
        return value
    except (ValueError,TypeError):
        return {'issues':['Invalid installed software observation; no versions assumed.'], 'environments':[], 'tools':[], 'components':[], 'system_packages':[]}
