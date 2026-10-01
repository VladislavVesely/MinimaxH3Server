"""Local, temporary MP4 stream-copy workspace. Never modifies source originals."""
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import uuid

from h3server.controller.ssh import ControllerError

MAX_FILE = 64 * 1024**2
MAX_WORKSPACE = 1024**3
MAX_CLIPS = 32
FIELDS = ('codec_type', 'codec_name', 'codec_tag_string', 'profile', 'level',
          'width', 'height', 'pix_fmt', 'sample_aspect_ratio', 'r_frame_rate',
          'time_base', 'field_order', 'color_range', 'color_space', 'color_transfer',
          'color_primaries', 'chroma_location', 'sample_fmt', 'sample_rate',
          'channels', 'channel_layout', 'extradata_hash', 'side_data_list')


class JoinerError(ControllerError):
    def __init__(self, code, status_code=422, *, fields=()):
        super().__init__(code, status_code)
        self.fields = list(fields)


def tool(name):
    found = shutil.which(name)
    if found:
        return found
    for base in ('/opt/homebrew/bin', '/usr/local/bin', '/usr/bin'):
        path = Path(base) / name
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def run(args, *, timeout=30, capture=False):
    try:
        result = subprocess.run(args, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise JoinerError('joiner_timeout', 504) from None
    except OSError:
        raise JoinerError('joiner_tools_missing', 409) from None
    if result.returncode:
        raise JoinerError('joiner_media_invalid')
    if capture:
        if len(result.stdout) > 65536:
            raise JoinerError('joiner_media_invalid')
        try:
            return json.loads(result.stdout)
        except (ValueError, UnicodeError):
            raise JoinerError('joiner_media_invalid') from None


def probe(path, ffprobe):
    entries = ','.join(k for k in FIELDS if k != 'side_data_list')
    args = [ffprobe, '-v', 'error', '-protocol_whitelist', 'file', '-f', 'mov',
            '-enable_drefs', '0', '-use_absolute_path', '0']
    data = run(args + ['-count_packets', '-show_data_hash', 'sha256', '-show_entries',
               'stream='+entries+',nb_read_packets,start_time:stream_side_data:format=duration',
               '-of', 'json', str(path)], capture=True)
    streams = data.get('streams', [])
    try:
        duration = float(data['format']['duration'])
        if not math.isfinite(duration) or not 0 < duration <= 3600:
            raise ValueError()
        if len(streams) not in (1, 2) or streams[0]['codec_type'] != 'video':
            raise ValueError()
        if streams[0]['codec_name'] not in ('h264', 'hevc'):
            raise ValueError()
        if len(streams) == 2 and (streams[1]['codec_type'] != 'audio' or streams[1]['codec_name'] != 'aac'):
            raise ValueError()
        for stream in streams:
            if int(stream['nb_read_packets']) <= 0 or not stream.get('extradata_hash'):
                raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise JoinerError('joiner_format_unsupported') from None
    first = run(args + ['-select_streams', 'v:0', '-read_intervals', '%+#1',
                        '-show_entries', 'packet=flags', '-of', 'json', str(path)], capture=True)
    if not first.get('packets') or 'K' not in first['packets'][0].get('flags', ''):
        raise JoinerError('joiner_keyframe_required')
    return {'streams': streams, 'duration': duration}


class Joiner:
    def __init__(self):
        self.lock = threading.Lock()
        self.clips = {}
        self.exports = {}
        self._temporary = None
        self._closed = False

    @contextmanager
    def operation(self):
        if not self.lock.acquire(blocking=False):
            raise JoinerError('joiner_busy', 409)
        try:
            if self._closed:
                raise JoinerError('joiner_closed', 409)
            yield
        finally:
            self.lock.release()

    def status(self):
        return {'available': bool(tool('ffmpeg') and tool('ffprobe')),
                'retained_clips': len(self.clips), 'retained_exports': len(self.exports), 'conversion_available': True,
                'max_file_bytes': MAX_FILE, 'max_clips': MAX_CLIPS}

    def _tools(self):
        tools = tool('ffmpeg'), tool('ffprobe')
        if not all(tools):
            raise JoinerError('joiner_tools_missing', 409)
        return tools

    def _root(self):
        if self._temporary is None:
            self._temporary = tempfile.TemporaryDirectory(prefix='h3-joiner-')
        return Path(self._temporary.name)

    def _quota(self, additional):
        used = sum(record['size'] for record in (*self.clips.values(), *self.exports.values()))
        if used + additional > MAX_WORKSPACE:
            raise JoinerError('joiner_storage_limit', 413)

    def add(self, data):
        with self.operation():
            _, ffprobe = self._tools()
            if not isinstance(data, bytes) or not 0 < len(data) <= MAX_FILE:
                raise JoinerError('joiner_file_limit', 413)
            if len(self.clips) >= MAX_CLIPS:
                raise JoinerError('joiner_clip_limit', 413)
            if data[4:8] != b'ftyp':
                raise JoinerError('joiner_format_unsupported')
            self._quota(len(data))
            id = uuid.uuid4().hex
            path = self._root() / (id + '.mp4')
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                info = probe(path, ffprobe)
                record = {'id': id, 'size': len(data), **info}
                self.clips[id] = record
                return self.public(record)
            finally:
                if id not in self.clips:
                    path.unlink(missing_ok=True)

    @staticmethod
    def public(record):
        video = record['streams'][0]
        return {k: record[k] for k in ('id', 'size', 'duration')} | {
            'width': video['width'], 'height': video['height'],
            'codec': video['codec_name'], 'audio': len(record['streams']) == 2, 'frame_rate':video.get('r_frame_rate')}

    def path(self, id, *, export=False):
        records = self.exports if export else self.clips
        if type(id) is not str or not re.fullmatch('[a-f0-9]{32}', id) or id not in records:
            raise JoinerError('joiner_clip_missing', 404)
        path = self._root() / (id + '.mp4')
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size != records[id]['size']:
            raise JoinerError('joiner_clip_missing', 404)
        return path

    def remove(self, id):
        with self.operation():
            path = self.path(id)
            path.unlink()
            del self.clips[id]

    def export(self, ids, *, convert=False):
        with self.operation():
            ffmpeg, ffprobe = self._tools()
            if not isinstance(ids, list) or not 2 <= len(ids) <= MAX_CLIPS:
                raise JoinerError('joiner_selection_invalid')
            paths = [self.path(id) for id in ids]
            records = [self.clips[id] for id in ids]
            if sum(r['duration'] for r in records) > 3600:
                raise JoinerError('joiner_duration_limit')
            baseline = records[0]['streams']
            differences = set()
            for record in records[1:]:
                if len(record['streams']) != len(baseline):
                    differences.add('audio/video track layout')
                for actual, expected in zip(record['streams'], baseline):
                    for field in FIELDS:
                        if actual.get(field) != expected.get(field):
                            differences.add('codec configuration' if field == 'extradata_hash' else field)
            if differences:
                if convert is True:
                    return self._convert(paths, records, ffmpeg, ffprobe)
                raise JoinerError('joiner_incompatible', fields=sorted(differences))
            budget = sum(r['size'] for r in records) + 1024**2
            self._quota(budget)
            id = uuid.uuid4().hex
            output = self._root() / (id + '.mp4')
            listing = self._root() / (id + '.ffconcat')
            try:
                # Only generated hex names enter the concat file. Originals were
                # forced through the MOV demuxer; no caller paths or URLs.
                with os.fdopen(os.open(listing, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
                    stream.write('ffconcat version 1.0\n')
                    for path in paths:
                        stream.write(f"file '{path.name}'\n")
                os.close(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
                run([ffmpeg, '-nostdin', '-v', 'error', '-y', '-protocol_whitelist', 'file',
                     '-f', 'concat', '-safe', '1', '-auto_convert', '0', '-i', str(listing),
                     '-map', '0', '-c', 'copy', '-map_chapters', '-1', '-movflags', '+faststart',
                     '-fs', str(budget), str(output)], timeout=120)
                size = output.stat().st_size
                if not 0 < size <= budget:
                    raise JoinerError('joiner_export_failed')
                info = probe(output, ffprobe)
                if len(info['streams']) != len(baseline) or any(
                    int(s['nb_read_packets']) != sum(int(r['streams'][i]['nb_read_packets']) for r in records)
                    or any(s.get(field) != baseline[i].get(field)
                           for field in FIELDS if field not in ('time_base', 'r_frame_rate'))
                    for i, s in enumerate(info['streams'])
                ):
                    raise JoinerError('joiner_export_failed')
                record = {'id': id, 'size': size, **info}
                self.exports[id] = record
                return self.public(record)
            finally:
                listing.unlink(missing_ok=True)
                if id not in self.exports:
                    output.unlink(missing_ok=True)

    def _convert(self, paths, records, ffmpeg, ffprobe):
        """Explicit lossy fallback only; called while holding the workspace lock."""
        from fractions import Fraction
        first = records[0]['streams'][0]
        colors = ('color_range', 'color_space', 'color_transfer', 'color_primaries')
        try:
            rate = Fraction(first['r_frame_rate'])
            width, height = int(first['width']), int(first['height'])
            if not 0 < rate <= 60:raise ValueError()
            for record in records:
                video = record['streams'][0]
                w, h = int(video['width']), int(video['height'])
                if not (0 < w <= 1920 and 0 < h <= 1920 and w*h <= 1920*1088):raise ValueError()
                if video.get('pix_fmt') not in ('yuv420p','yuvj420p','yuv422p','yuv444p','nv12'):raise ValueError()
                if video.get('sample_aspect_ratio') not in (None,'1:1','0:1'):raise ValueError()
                if video.get('side_data_list') or video.get('color_transfer') in ('smpte2084','arib-std-b67'):raise ValueError()
                if any(video.get(k) != first.get(k) for k in colors):raise ValueError()
                # Do not silently shift audio/video with nonzero starting offsets.
                for stream in record['streams']:
                    start = float(stream.get('start_time', 0))
                    if not math.isfinite(start) or abs(start) > .002:raise ValueError()
        except (KeyError, ValueError, TypeError, ZeroDivisionError):
            raise JoinerError('joiner_conversion_unsupported') from None
        width += width % 2; height += height % 2
        duration = sum(r['duration'] for r in records)
        budget = math.ceil(duration * (12000000 + 192000) / 8) + 4*1024**2
        self._quota(budget)
        id = uuid.uuid4().hex
        output = self._root() / (id + '.mp4')
        audio = any(len(r['streams']) == 2 for r in records)
        args = [ffmpeg, '-nostdin', '-v', 'error', '-y', '-filter_complex_threads', '2']
        filters = []; inputs = []
        for i, (path, record) in enumerate(zip(paths, records)):
            args += ['-threads', '2', '-protocol_whitelist', 'file', '-f', 'mov',
                     '-enable_drefs', '0', '-use_absolute_path', '0', '-i', str(path)]
            seconds = format(record['duration'], '.9f')
            filters.append(f'[{i}:v:0]setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,'
                           f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={rate},'
                           f'tpad=stop_mode=clone:stop_duration={seconds},trim=duration={seconds}[v{i}]')
            inputs.append(f'[v{i}]')
            if audio:
                if len(record['streams']) == 2:
                    filters.append(f'[{i}:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,'
                                   f'asetpts=PTS-STARTPTS,apad,atrim=duration={seconds}[a{i}]')
                else:
                    filters.append(f'anullsrc=r=48000:cl=stereo:d={seconds}[a{i}]')
                inputs.append(f'[a{i}]')
        filters.append(''.join(inputs) + f'concat=n={len(records)}:v=1:a={int(audio)}[v]' + ('[a]' if audio else ''))
        args += ['-filter_complex', ';'.join(filters), '-map', '[v]', '-c:v', 'libx264',
                 '-threads', '2', '-preset', 'medium', '-crf', '18', '-maxrate', '12M', '-bufsize', '24M', '-pix_fmt', 'yuv420p']
        if audio:args += ['-map', '[a]', '-c:a', 'aac', '-b:a', '192k']
        args += ['-map_metadata', '-1', '-map_chapters', '-1', '-movflags', '+faststart', '-fs', str(budget), str(output)]
        try:
            os.close(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            run(args, timeout=150)
            size = output.stat().st_size
            if not 0 < size < budget:raise JoinerError('joiner_export_failed')
            info = probe(output, ffprobe)
            video = info['streams'][0]
            if (video['width'],video['height'],video['codec_name']) != (width,height,'h264'):
                raise JoinerError('joiner_export_failed')
            if len(info['streams']) != 1+int(audio) or abs(info['duration']-duration) > max(.15,len(records)/float(rate)+.05):
                raise JoinerError('joiner_export_failed')
            if audio and (info['streams'][1]['codec_name'] != 'aac' or int(info['streams'][1]['sample_rate']) != 48000):
                raise JoinerError('joiner_export_failed')
            record = {'id':id,'size':size,**info}
            self.exports[id] = record
            return self.public(record) | {'converted':True}
        finally:
            if id not in self.exports:output.unlink(missing_ok=True)

    def clear(self):
        with self.operation():
            self._cleanup()

    def _cleanup(self):
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        self.clips.clear()
        self.exports.clear()

    def close(self):
        with self.lock:
            self._closed = True
            self._cleanup()
