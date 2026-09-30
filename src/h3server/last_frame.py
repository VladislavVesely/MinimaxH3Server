"""Bounded, read-only export of the final decoded video frame.

Generated H3 videos are 8-bit. PNG uses stored (level-zero) DEFLATE blocks:
no resizing, palette conversion, JPEG encoding or additional quantization.
"""
import io
import os
import subprocess
import sys

MAX_PNG_BYTES = 32 * 1024 * 1024


def decode(stream):
    import av
    with av.open(stream, options={'protocol_whitelist':'file,pipe','enable_drefs':'0','use_absolute_path':'0'}) as container:
        if len(container.streams.video) != 1:
            raise ValueError('Expected one video stream')
        video = container.streams.video[0]
        if not 0 < video.width <= 1920 or not 0 < video.height <= 1920 or video.width * video.height > 1920 * 1088:
            raise ValueError('Invalid canvas')
        video.codec_context.thread_count = 2
        last = None
        for count, frame in enumerate(container.decode(video), 1):
            if count > 345 or (frame.width, frame.height) != (video.width, video.height):
                raise ValueError('Invalid frame geometry or count')
            # Never silently reduce precision from a future higher-bit-depth output.
            if any(component.bits > 8 for component in frame.format.components):
                raise ArithmeticError('High-bit-depth export requires a precision-preserving encoder')
            last = frame
        if last is None:
            raise ValueError('No video frames')
        result = io.BytesIO()
        last.to_image().save(result, format='PNG', compress_level=0, optimize=False)
        return result.getvalue()


def extract(fd):
    try:
        result = subprocess.run([sys.executable, '-m', 'h3server.last_frame', str(fd)],
                                pass_fds=(fd,), capture_output=True, timeout=25, check=False)
    except subprocess.TimeoutExpired:
        raise TimeoutError('Frame extraction timed out') from None
    if result.returncode == 3:
        raise ArithmeticError('High-bit-depth export is not supported without precision loss')
    if result.returncode or not result.stdout.startswith(b'\x89PNG\r\n\x1a\n') or not 0 < len(result.stdout) <= MAX_PNG_BYTES:
        raise ValueError('Frame extraction failed')
    return result.stdout


if __name__ == '__main__':
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (20,20))
        if sys.platform.startswith('linux'):
            resource.setrlimit(resource.RLIMIT_AS, (3*1024**3,3*1024**3))
        if len(sys.argv) != 2:
            raise ValueError('Expected owned descriptor')
        with os.fdopen(os.dup(int(sys.argv[1])), 'rb') as stream:
            data = decode(stream)
        if len(data) > MAX_PNG_BYTES:
            raise ValueError('Image too large')
        sys.stdout.buffer.write(data)
    except ArithmeticError:
        raise SystemExit(3) from None
    except Exception:
        raise SystemExit(1) from None
