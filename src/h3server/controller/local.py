"""Local counterparts of the bounded channel/file transport, not a shell HTTP API.

Only SSHConnection's existing fixed command builders call these primitives.
Privileged/staging guardians retain their locks after EOF exactly as over SSH.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import select
import signal
import socket
import stat
import subprocess
import threading
import time


class LocalChannel:
    def __init__(self, *, guardian=False):
        self.guardian=guardian;self.process=None;self.closed=False;self.timeout=30
        self.eof=set()

    def settimeout(self,value):self.timeout=value

    def exec_command(self,command):
        if self.process is not None or self.closed:raise OSError('channel already used')
        env={k:v for k,v in os.environ.items() if k in {'HOME','USER','LOGNAME','PATH','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS','LANG','TZ'}}
        import pwd
        account=pwd.getpwuid(os.getuid())
        env.update(HOME=account.pw_dir,USER=account.pw_name,LOGNAME=account.pw_name,LC_ALL='C')
        self.process=subprocess.Popen(['/bin/sh','-c',command],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            env=env,cwd='/',start_new_session=True,bufsize=0)
        for stream in (self.process.stdin,self.process.stdout,self.process.stderr):os.set_blocking(stream.fileno(),False)

    def _ready(self,name):
        if self.closed or not self.process or name in self.eof:return False
        return bool(select.select([getattr(self.process,name)],[],[],0)[0])

    def recv_ready(self):return self._ready('stdout')
    def recv_stderr_ready(self):return self._ready('stderr')

    def _recv(self,name,count):
        stream=getattr(self.process,name)
        if not select.select([stream],[],[],self.timeout)[0]:raise TimeoutError()
        data=os.read(stream.fileno(),count)
        if not data:self.eof.add(name)
        return data

    def recv(self,count):return self._recv('stdout',count)
    def recv_stderr(self,count):return self._recv('stderr',count)

    def send(self,data):
        if self.closed:raise OSError('closed channel')
        if not select.select([],[self.process.stdin],[],self.timeout)[1]:raise TimeoutError()
        return os.write(self.process.stdin.fileno(),data)

    def sendall(self,data):
        remaining=memoryview(data)
        while remaining:remaining=remaining[self.send(remaining):]

    def shutdown_write(self):
        if self.process and not self.process.stdin.closed:self.process.stdin.close()

    def exit_status_ready(self):return self.closed or (self.process is not None and self.process.poll() is not None)
    def recv_exit_status(self):
        deadline=time.monotonic()+self.timeout
        while not self.closed and self.process.poll() is None:
            if time.monotonic()>=deadline:raise TimeoutError()
            time.sleep(.005)
        status=self.process.poll()
        return status if status is not None else -1

    def close(self):
        if self.closed:return
        self.closed=True
        if self.process is None:return
        for stream in (self.process.stdin,self.process.stdout,self.process.stderr):
            try:stream.close()
            except OSError:pass
        if not self.guardian and self.process.poll() is None:
            try:os.killpg(self.process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:self.process.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                try:os.killpg(self.process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
        # A guardian processes EOF only after its current privileged pipeline.
        # Never kill it or release its ownership lock on a browser disconnect.
        threading.Thread(target=self.process.wait,daemon=True,name='h3-local-reap').start()


class LocalTransport:
    def __init__(self):
        self.active=True;self.guardian=False;self.resources=[];self.lock=threading.RLock()
    def is_active(self):return self.active
    def is_authenticated(self):return self.active
    def _prune(self):
        self.resources=[r for r in self.resources if (r.fileno()>=0 if isinstance(r,socket.socket) else not r.closed)]
    def open_session(self,timeout=30):
        with self.lock:
            if not self.active:raise OSError('local transport closed')
            self._prune()
            channel=LocalChannel(guardian=self.guardian);channel.settimeout(timeout);self.resources.append(channel);return channel
    def open_channel(self,kind,address,source,timeout=10):
        if kind!='direct-tcpip' or address!=('127.0.0.1',8765):raise ValueError('local API destination forbidden')
        with self.lock:
            if not self.active:raise OSError('local transport closed')
            self._prune()
            channel=socket.create_connection(address,timeout=timeout);self.resources.append(channel);return channel
    def close(self):
        with self.lock:
            self.active=False
            for resource in self.resources:
                try:
                    if isinstance(resource,socket.socket):resource.shutdown(socket.SHUT_RDWR)
                except OSError:pass
                try:resource.close()
                except OSError:pass
            self.resources=[]


class LocalFiles:
    """SFTP-shaped, descriptor-anchored access to the single owned install root."""
    def __init__(self,root):self.root=Path(root).absolute();self.streams=[];self.closed=False

    @contextmanager
    def _parent(self,path,*,write=False):
        if self.closed:raise OSError('file transport closed')
        path=Path(path)
        if not path.is_absolute() or '..' in path.parts:raise ValueError('invalid local path')
        owned=path==self.root or self.root in path.parents
        if not owned and (write or path not in self.root.parents):raise ValueError('path outside local install root')
        fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
        current=Path('/')
        try:
            for part in path.parent.parts[1:]:
                child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.close(fd);fd=child;current=current/part
                info=os.fstat(fd)
                if (current==self.root or self.root in current.parents) and (info.st_uid!=os.getuid() or info.st_mode&0o077):
                    raise ValueError('unsafe local directory')
            yield fd,path.name
        finally:os.close(fd)

    def lstat(self,path):
        with self._parent(path) as (fd,name):return os.stat(name,dir_fd=fd,follow_symlinks=False)
    def mkdir(self,path,mode=0o700):
        with self._parent(path,write=True) as (fd,name):os.mkdir(name,mode,dir_fd=fd)
    def chmod(self,path,mode):
        with self._parent(path,write=True) as (fd,name):
            target=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
            try:
                info=os.fstat(target)
                if info.st_uid!=os.getuid() or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):raise ValueError('unsafe local object')
                if stat.S_ISREG(info.st_mode) and info.st_nlink!=1:raise ValueError('linked local object')
                os.fchmod(target,mode)
            finally:os.close(target)
    def open(self,path,mode='rb'):
        flags={'rb':os.O_RDONLY,'wx':os.O_WRONLY|os.O_CREAT|os.O_EXCL}.get(mode)
        if flags is None:raise ValueError('unsupported local file mode')
        with self._parent(path,write=mode!='rb') as (parent,name):
            fd=os.open(name,flags|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=parent)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1 or info.st_mode&0o077:raise ValueError('unsafe local file')
            stream=os.fdopen(fd,'rb' if mode=='rb' else 'wb');fd=None
            self.streams.append(stream);return stream
        finally:
            if fd is not None:os.close(fd)
    def listdir(self,path):
        with self._parent(path) as (fd,name):
            child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            try:return os.listdir(child)
            finally:os.close(child)
    def remove(self,path):
        with self._parent(path,write=True) as (fd,name):os.unlink(name,dir_fd=fd)
    def rename(self,source,target):
        with self._parent(source,write=True) as (a,s),self._parent(target,write=True) as (b,t):
            info=os.stat(s,dir_fd=a,follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1:raise ValueError('unsafe rename source')
            # Atomic no-replace publication; drop the temporary link immediately.
            os.link(s,t,src_dir_fd=a,dst_dir_fd=b,follow_symlinks=False)
            os.unlink(s,dir_fd=a)
    def posix_rename(self,source,target):
        with self._parent(source,write=True) as (a,s),self._parent(target,write=True) as (b,t):os.replace(s,t,src_dir_fd=a,dst_dir_fd=b)
    def close(self):
        self.closed=True
        for stream in self.streams:stream.close()
        self.streams=[]
