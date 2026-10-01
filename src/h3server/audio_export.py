"""Read-only, resource-bounded MP3 export of an owned generated video's audio.

MP3 is a lossy 320 kbps re-encode, not stream copy. Original video is untouched.
"""
import io
import os
import subprocess
import sys

MAX_MP3_BYTES = 4 * 1024 * 1024


class NoAudio(ValueError):
    pass


class BoundedBuffer(io.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_MP3_BYTES:
            raise ValueError('Audio export exceeds size limit')
        return super().write(data)


def encode(source):
    import av
    with av.open(source, options={'protocol_whitelist':'file,pipe','enable_drefs':'0','use_absolute_path':'0'}) as video:
        if not video.streams.audio:
            raise NoAudio('Video has no audio track')
        if len(video.streams.audio) != 1:
            raise ValueError('Expected one audio track')
        audio = video.streams.audio[0]
        channels = len(audio.codec_context.layout.channels)
        if channels not in (1, 2):
            raise ValueError('Only mono/stereo audio is supported')
        layout = 'mono' if channels == 1 else 'stereo'
        rate = audio.codec_context.sample_rate
        rate = rate if rate in (32000, 44100, 48000) else 48000
        audio.codec_context.thread_count = 2
        data = BoundedBuffer()
        with av.open(data, 'w', format='mp3') as output:
            stream = output.add_stream('libmp3lame', rate=rate)
            stream.layout = layout
            stream.bit_rate = 320000
            resampler = av.AudioResampler(format='fltp', layout=layout, rate=rate)
            seconds = 0
            def emit(frames):
                for frame in frames:
                    frame.pts = None
                    for packet in stream.encode(frame):output.mux(packet)
            for count, frame in enumerate(video.decode(audio), 1):
                if frame.sample_rate <= 0:raise ValueError('Invalid sample rate')
                seconds += frame.samples / frame.sample_rate
                if seconds > 60 or count > 10000:raise ValueError('Audio duration exceeds export limit')
                emit(resampler.resample(frame))
            if seconds == 0:raise NoAudio('No decoded audio')
            emit(resampler.resample(None))
            for packet in stream.encode(None):output.mux(packet)
        return data.getvalue()


def extract(fd):
    try:
        result = subprocess.run([sys.executable, '-m', 'h3server.audio_export', str(fd)],
                                pass_fds=(fd,), capture_output=True, timeout=25, check=False)
    except subprocess.TimeoutExpired:
        raise TimeoutError('Audio export timed out') from None
    if result.returncode == 3:raise NoAudio('Video has no audio track')
    if result.returncode or not result.stdout.startswith(b'ID3') or not 0 < len(result.stdout) <= MAX_MP3_BYTES:
        raise ValueError('Audio export failed')
    return result.stdout


if __name__ == '__main__':
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
        if sys.platform.startswith('linux'):resource.setrlimit(resource.RLIMIT_AS, (3*1024**3, 3*1024**3))
        if len(sys.argv) != 2:raise ValueError('Expected owned descriptor')
        with os.fdopen(os.dup(int(sys.argv[1])), 'rb') as source:data = encode(source)
        sys.stdout.buffer.write(data)
    except NoAudio:raise SystemExit(3) from None
    except Exception:raise SystemExit(1) from None
