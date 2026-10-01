"""Regression: a running native controller survives replacement of its executable.

Uses only an isolated copy and fresh state; no SSH connection or remote mutations.
"""
import http.cookiejar
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor, ProxyHandler


def smoke(executable):
    with tempfile.TemporaryDirectory(prefix='h3-replacement-smoke-') as temp:
        target=Path(temp)/Path(executable).name
        shutil.copy2(executable,target)
        process=subprocess.Popen([str(target),'--no-browser','--state-dir',str(Path(temp)/'state')],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=temp)
        lines=queue.Queue()
        threading.Thread(target=lambda:lines.put(process.stdout.readline()),daemon=True).start()
        try:
            launch=lines.get(timeout=60).strip()
            assert '/#token=' in launch,'Missing private launch URL'
            origin,token=launch.split('/#token=')
            client=build_opener(ProxyHandler({}),HTTPCookieProcessor(http.cookiejar.CookieJar()))
            def request(path,data,headers):
                try:r=client.open(Request(origin+path,data=data,headers=headers),timeout=15)
                except HTTPError as e:r=e
                with r:return r.status,r.read()
            status,body=request('/api/v1/controller/bootstrap',json.dumps({'token':token}).encode(),
                {'Origin':origin,'Content-Type':'application/json'})
            assert status==200
            csrf=json.loads(body)['csrf_token']
            # Replace only our scratch copy, never a live user controller executable.
            replacement=Path(temp)/'replacement';replacement.write_bytes(b'replacement fixture')
            os.replace(replacement,target)
            status,body=request('/api/v1/controller/remote/assets/image',b'fixture',
                {'Origin':origin,'X-CSRF-Token':csrf,'Content-Type':'application/octet-stream'})
            assert status==409,(status,body[:120])
            assert json.loads(body)['error']['code']=='authentication_required'
            print('PASS: late upload imports survive executable replacement; normal authentication rejection, no target contacted.')
        finally:
            process.send_signal(signal.SIGINT)
            try:process.communicate(timeout=15)
            except subprocess.TimeoutExpired:process.kill();process.communicate(timeout=10)


if __name__=='__main__':smoke(str(Path(sys.argv[1]).resolve()))
