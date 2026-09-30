"""Owner-only immutable media. Logical SHA256 IDs, never caller paths/URLs.

Only the bounded probe child imports codecs in the daemon. The inference child
rechecks content identities before decoding. Original bytes survive job history.
"""
from contextlib import contextmanager
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from h3server.backends.base import directory, regular_file, atomic_json, read_json

MAX_BYTES = 64 * 1024 * 1024
MAX_STORE = 8 * 1024**3
KINDS = ('image', 'video', 'audio')
FIELDS = ('reference_images', 'reference_videos', 'reference_audios', 'first_frame', 'last_frame')
_verified = {}  # Bounded unchanged-file hash cache; never trusts mtime alone.

def asset_id(value):
    if type(value) is not str or not re.fullmatch('[a-f0-9]{64}',value):raise ValueError('Invalid asset ID')
    return value


def sniff(stream):
    stream.seek(0); magic=stream.read(16);stream.seek(0)
    if magic.startswith(b'\x89PNG\r\n\x1a\n'):return 'image','image/png',None
    if magic.startswith(b'\xff\xd8\xff'):return 'image','image/jpeg',None
    if magic.startswith(b'RIFF') and magic[8:12]==b'WEBP':return 'image','image/webp',None
    if magic[4:8]==b'ftyp':return 'video','video/mp4','mov'
    if magic.startswith(b'\x1aE\xdf\xa3'):return 'video','video/webm','matroska'
    if magic.startswith(b'RIFF') and magic[8:12]==b'WAVE':return 'audio','audio/wav','wav'
    if magic.startswith(b'fLaC'):return 'audio','audio/flac','flac'
    if magic.startswith(b'ID3') or (len(magic)>1 and magic[0]==255 and magic[1]&224==224):return 'audio','audio/mpeg','mp3'
    raise ValueError('Unsupported media: PNG/JPEG/WebP, MP4/WebM, WAV/FLAC/MP3 only')


def inspect_media(stream):
    kind,mime,format=sniff(stream)
    if kind=='image':
        from PIL import Image, ImageOps
        import warnings
        Image.MAX_IMAGE_PIXELS=16_777_216
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(stream) as image:
                if getattr(image,'n_frames',1)!=1:raise ValueError('Animated images are not frame assets')
                if max(image.size)>8192 or image.width*image.height>16_777_216:raise ValueError('Image geometry limit')
                image.load();image=ImageOps.exif_transpose(image)
                return dict(kind=kind,mime=mime,width=image.width,height=image.height,duration=0,frames=1)
    import av
    with av.open(stream,format=format,options={'protocol_whitelist':'file,pipe','enable_drefs':'0','use_absolute_path':'0'}) as container:
        streams=container.streams.video if kind=='video' else container.streams.audio
        if len(streams)!=1:raise ValueError('Expected one media stream')
        s=streams[0];count=0;duration=0
        if kind=='video':
            if not 0<float(s.average_rate or 0)<=60:raise ValueError('Video must be at most 60fps')
            if not 1<=s.width<=1920 or not 1<=s.height<=1920 or s.width*s.height>1920*1088:raise ValueError('Video geometry limit: at most 1920×1088 area')
            first=None;previous=-1
            for frame in container.decode(s):
                count+=1
                if frame.width!=s.width or frame.height!=s.height:raise ValueError('Variable video geometry')
                t=float(frame.time) if frame.time is not None else (count-1)/float(s.average_rate)
                if first is None:first=t
                t-=first
                if t<previous:raise ValueError('Non-monotonic video timestamps')
                previous=t;duration=t+1/float(s.average_rate)
                if count>900 or duration>15.05:raise ValueError('Video exceeds 15 seconds')
            if count<5 or duration<.2:raise ValueError('Video must contain at least 5 frames')
            return dict(kind=kind,mime=mime,width=s.width,height=s.height,duration=duration,frames=count)
        samples=0;rate=None
        for frame in container.decode(s):
            if not 8000<=frame.sample_rate<=192000 or len(frame.layout.channels)>2:raise ValueError('Unsupported audio rate/channels')
            if rate is not None and rate!=frame.sample_rate:raise ValueError('Variable audio sample rate')
            rate=frame.sample_rate;samples+=frame.samples;duration=samples/rate
            if duration>15.05:raise ValueError('Audio exceeds 15 seconds')
        if duration<.2:raise ValueError('Audio must be at least 0.2 seconds')
        return dict(kind=kind,mime=mime,width=0,height=0,duration=duration,frames=0)


def probe(fd):
    try:
        r=subprocess.run([sys.executable,'-m','h3server.assets','--probe',str(fd)],pass_fds=(fd,),capture_output=True,timeout=60,check=False)
        if r.returncode or len(r.stdout)>4096:raise ValueError('Invalid or oversized media; supported formats and limits are shown beside the upload')
        value=json.loads(r.stdout)
        if value.get('kind') not in KINDS:raise ValueError('Invalid media probe')
        return value
    except (subprocess.TimeoutExpired,json.JSONDecodeError):raise ValueError('Media validation failed or exceeded its time limit') from None


class AssetStore:
    def __init__(self,paths):self.paths=paths

    @contextmanager
    def open(self,id):
        id=asset_id(id)
        with regular_file(self.paths,'assets/'+id+'.bin') as stream:
            info=os.fstat(stream.fileno())
            signature=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
            key=(str(self.paths.root),id)
            if not 0<info.st_size<=MAX_BYTES:raise ValueError('Asset content changed')
            if _verified.get(key)!=signature:
                digest=hashlib.file_digest(stream,'sha256').hexdigest();after=os.fstat(stream.fileno())
                if digest!=id or (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)!=signature:
                    raise ValueError('Asset content changed')
                if len(_verified)>=1024:_verified.clear()
                _verified[key]=signature
            stream.seek(0);yield stream

    def metadata(self,id):
        id=asset_id(id)
        value=read_json(self.paths,'assets/'+id+'.json')
        with self.open(id) as stream:
            if value.get('id')!=id or value.get('size')!=os.fstat(stream.fileno()).st_size or value.get('kind') not in KINDS:
                raise ValueError('Asset metadata changed')
        return value

    def put(self,data,kind):
        if kind not in KINDS or not 0<len(data)<=MAX_BYTES:raise ValueError('Invalid asset kind or size (64 MiB maximum)')
        import fcntl
        id=hashlib.sha256(data).hexdigest()
        with regular_file(self.paths,'state/assets.lock',flags=os.O_CREAT|os.O_RDWR,mode='r+b',create_parents=True) as guard:
            fcntl.flock(guard,fcntl.LOCK_EX)
            with directory(self.paths,'assets',create=True) as root:
                try:
                    info=self.metadata(id)
                    if info['kind']!=kind:raise ValueError('Asset kind does not match selected slot')
                    return info
                except FileNotFoundError:pass
                used=sum(os.stat(n,dir_fd=root,follow_symlinks=False).st_size for n in os.listdir(root) if n.endswith('.bin'))
                if used+len(data)>MAX_STORE:raise ValueError('Reference asset storage limit reached (8 GiB)')
                name=uuid.uuid4().hex+'.upload'
                try:
                    with regular_file(self.paths,'assets/'+name,flags=os.O_CREAT|os.O_EXCL|os.O_RDWR,mode='w+b') as stream:
                        stream.write(data);stream.flush();os.fsync(stream.fileno());stream.seek(0)
                        info=probe(stream.fileno())
                    if info['kind']!=kind:raise ValueError('Asset kind does not match selected slot')
                    info.update(id=id,size=len(data))
                    try:os.link(name,id+'.bin',src_dir_fd=root,dst_dir_fd=root,follow_symlinks=False)
                    except FileExistsError:
                        with self.open(id):pass
                    os.unlink(name,dir_fd=root)
                    atomic_json(self.paths,'assets/'+id+'.json',info);os.fsync(root)
                    return info
                finally:
                    try:os.unlink(name,dir_fd=root)
                    except FileNotFoundError:pass

    def conditioning(self,settings):
        result={};video_seconds=0
        for name in FIELDS:
            values=getattr(settings,name)
            if name in ('first_frame','last_frame'):values=(values,) if values else ()
            expected='image' if name in ('first_frame','last_frame','reference_images') else 'video' if name=='reference_videos' else 'audio'
            result[name]=[]
            for id in values:
                info=self.metadata(id)
                if info['kind']!=expected:raise ValueError('Asset kind does not match conditioning slot')
                result[name].append(info)
                if expected=='video':video_seconds+=info['duration']
        if video_seconds>15.1:raise ValueError('Reference videos must total at most 15 seconds')
        return result


if __name__=='__main__':
    try:
        if len(sys.argv)!=3 or sys.argv[1]!='--probe':raise ValueError('Invalid probe invocation')
        import resource
        resource.setrlimit(resource.RLIMIT_CPU,(45,45))
        if sys.platform.startswith('linux'):resource.setrlimit(resource.RLIMIT_AS,(3*1024**3,3*1024**3))
        with os.fdopen(os.dup(int(sys.argv[2])),'rb') as stream:print(json.dumps(inspect_media(stream)))
    except Exception:
        raise SystemExit(1) from None
