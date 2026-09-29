import hashlib
import io
import json

import pytest

from h3server.paths import AppPaths
from h3server import downloads


def inventory(data=b"model data", lfs=True):
    entry = {"rfilename": "vae/weights.safetensors", "size": len(data),
             "blobId": hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()}
    if lfs:
        entry["lfs"] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    return {"model": {"repository": "MiniMaxAI/MiniMax-H3", "revision": "a" * 40,
                      "files": [entry], "total_bytes": len(data),
                      "download_url_template": "https://huggingface.co/MiniMaxAI/MiniMax-H3/resolve/" + "a" * 40 + "/{path}"},
            "sources": {"license": {"sha256": "b" * 64}}}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    paths = AppPaths(tmp_path)
    for p in paths.directories():
        p.mkdir(parents=True, exist_ok=True)
    manifest = inventory()
    monkeypatch.setattr(downloads, "load_manifest", lambda: manifest)
    monkeypatch.setattr("h3server.backends.load_manifest", lambda: manifest)
    return paths, manifest


def approve(paths):
    downloads.record_consent(paths, license_sha256="b" * 64, license_acknowledged=True,
                             eligibility_confirmed=True, download_approved=True)


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {"Content-Length": str(len(data))}


def download_model(paths, *, opener, **kwargs):
    """Tiny in-memory response fixtures; blocking IO tests use the real child."""
    from urllib.request import Request
    def transport(paths, name, offset, *, timeout, **unused):
        revision = downloads.load_manifest()['model']['revision']
        request = Request(f'https://huggingface.co/MiniMaxAI/MiniMax-H3/resolve/{revision}/{name}',
                          headers={'Range': f'bytes={offset}-'} if offset else {})
        return opener(request, timeout=timeout)
    return downloads.download_model(paths, transport=transport, **kwargs)


def child_transport(tmp_path, manifest, opener_source):
    """Spawn normal Python; substitute only pinned inventory and network boundary."""
    import sys
    script = tmp_path / 'transport_fixture.py'
    script.write_text('import json, sys\nfrom urllib.request import Request, urlopen\n'
        'import h3server.backends as backends\n'
        f'backends.load_manifest = lambda: {manifest!r}\n'
        'from h3server._download_transport import _fetch\n' + opener_source + '\n'
        '_fetch(json.loads(sys.stdin.buffer.readline()), opener=opener)\n')
    from h3server._download_transport import DownloadTransport
    children = []
    def factory(*args, **kwargs):
        response = DownloadTransport(*args, **kwargs, command=(sys.executable, str(script)))
        children.append(response)
        return response
    return factory, children


@pytest.mark.parametrize("lfs", [True, False])
def test_download_hash_progress_and_atomic_promotion(setup, lfs):
    paths, manifest = setup
    manifest.update(inventory(lfs=lfs))
    approve(paths)
    events, calls = [], []
    def fetch(request, timeout):
        calls.append(request)
        return Response(b"model data")
    snapshot = download_model(paths, emit=events.append, opener=fetch)
    assert (snapshot / "vae/weights.safetensors").read_bytes() == b"model data"
    assert events[-1].status == "completed"
    assert events[-1].progress.completed == 10
    assert calls[0].full_url.endswith("a" * 40 + "/vae/weights.safetensors")
    assert downloads.verify_snapshot(paths) == snapshot
    download_model(paths, opener=lambda *a, **k: pytest.fail("already verified"))


def test_consent_required_before_any_network(setup):
    paths, _ = setup
    with pytest.raises(ValueError, match="consent"):
        download_model(paths, opener=lambda *a, **k: pytest.fail("network before consent"))
    with pytest.raises(ValueError):
        downloads.record_consent(paths, license_sha256="b" * 64, license_acknowledged=True,
                                 eligibility_confirmed=False, download_approved=True)


def test_consent_is_persisted_and_bound_to_revision_and_license(setup):
    paths, manifest = setup
    approve(paths)
    downloads.require_consent(paths)
    manifest["model"]["revision"] = "c" * 40
    with pytest.raises(ValueError, match="consent"):
        downloads.require_consent(paths)


@pytest.mark.parametrize("data,headers", [(b"bad!", None), (b"model dat!", None),
    (b"model data!", None), (b"model data", {"Content-Length": "999"}),
    (b"model data", {"Content-Encoding": "gzip"})])
def test_corruption_or_size_never_published(setup, data, headers):
    paths, _ = setup
    approve(paths)
    with pytest.raises(ValueError):
        download_model(paths, opener=lambda *a, **k: Response(data, headers=headers))
    assert not (downloads.snapshot_path(paths) / "vae/weights.safetensors").exists()


def test_cancel_and_validated_range_resume(setup):
    paths, _ = setup
    approve(paths)
    cancelled = [False]
    class Interrupted(Response):
        def read(self, size=-1):
            chunk = super().read(min(size, 5))
            cancelled[0] = True
            return chunk
    with pytest.raises(downloads.DownloadCancelled):
        download_model(paths, opener=lambda *a, **k: Interrupted(b"model data"),
                                 cancelled=lambda: cancelled[0])
    calls = []
    def resume(request, timeout):
        calls.append(request)
        return Response(b" data", 206, {"Content-Length": "5", "Content-Range": "bytes 5-9/10"})
    download_model(paths, opener=resume)
    assert calls[0].get_header("Range") == "bytes=5-"


@pytest.mark.parametrize("status,content_range", [(206, "bytes 4-9/10"), (206, "bytes 5-9/11"), (416, "bytes */10")])
def test_bad_resume_range_rejected(setup, status, content_range):
    paths, _ = setup
    approve(paths)
    with pytest.raises(ValueError):
        download_model(paths, opener=lambda *a, **k: Response(b"model"))
    with pytest.raises(ValueError):
        download_model(paths, opener=lambda *a, **k: Response(b" data", status, {"Content-Range": content_range}))


def test_server_ignoring_range_restarts_instead_of_appending(setup):
    paths, _ = setup
    approve(paths)
    with pytest.raises(ValueError):
        download_model(paths, opener=lambda *a, **k: Response(b"model"))
    download_model(paths, opener=lambda *a, **k: Response(b"model data"))
    assert downloads.verify_snapshot(paths)


@pytest.mark.parametrize("name", ["../escape", "/escape", "x/../../escape", "x\\escape", "model.bin"])
def test_manifest_paths_and_pickle_fail_closed(setup, name):
    paths, manifest = setup
    manifest["model"]["files"][0]["rfilename"] = name
    approve(paths)
    with pytest.raises(ValueError):
        download_model(paths, opener=lambda *a, **k: pytest.fail("unsafe inventory"))


def test_symlink_and_hardlink_refused(setup, tmp_path):
    paths, _ = setup
    approve(paths)
    outside = tmp_path / "outside"
    outside.mkdir()
    snapshot = downloads.snapshot_path(paths)
    snapshot.parent.mkdir(exist_ok=True)
    snapshot.symlink_to(outside, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        download_model(paths, opener=lambda *a, **k: pytest.fail("symlink"))


def test_extra_pickle_invalidates_snapshot(setup):
    paths, _ = setup
    approve(paths)
    snapshot = download_model(paths, opener=lambda *a, **k: Response(b"model data"))
    (snapshot / "pytorch_model.bin").write_bytes(b"untrusted")
    with pytest.raises(ValueError):
        downloads.verify_snapshot(paths)


def test_resume_corruption_fails_hash_then_can_restart(setup):
    paths, manifest = setup
    approve(paths)
    partial = paths.tmp_downloads / manifest['model']['revision'] / 'vae/weights.safetensors.part'
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b'wrong')
    with pytest.raises(ValueError, match='integrity'):
        download_model(paths, opener=lambda *a, **k: Response(b' data', 206, {'Content-Range':'bytes 5-9/10'}))
    assert partial.stat().st_size == 0
    download_model(paths, opener=lambda *a, **k: Response(b'model data'))


def test_hardlinked_partial_never_modified(setup, tmp_path):
    import os
    paths, manifest = setup
    approve(paths)
    partial = paths.tmp_downloads / manifest['model']['revision'] / 'vae/weights.safetensors.part'
    partial.parent.mkdir(parents=True)
    outside = tmp_path / 'valuable'
    outside.write_bytes(b'keep')
    os.link(outside, partial)
    with pytest.raises(ValueError, match='unaliased'):
        download_model(paths, opener=lambda *a, **k: pytest.fail('hardlink'))
    assert outside.read_bytes() == b'keep'


def test_hashing_is_cooperatively_cancellable(setup):
    paths, _ = setup
    approve(paths)
    download_model(paths, opener=lambda *a, **k: Response(b'model data'))
    with pytest.raises(downloads.DownloadCancelled):
        downloads.verify_snapshot(paths, cancelled=lambda: True)


@pytest.mark.parametrize('url', ['http://huggingface.co/weights', 'https://evil.example/weights',
    'https://huggingface.co.evil.example/weights', 'https://user:secret@huggingface.co/weights',
    'https://huggingface.co:8443/weights', 'file:///tmp/weights'])
def test_redirect_allowlist(url):
    from urllib.request import Request
    with pytest.raises(ValueError, match='redirect'):
        downloads._Redirects().redirect_request(Request('https://huggingface.co/start'), None, 302, '', {}, url)


def test_pinned_cdn_redirect_preserves_range():
    from urllib.request import Request
    request = Request('https://huggingface.co/start', headers={'Range':'bytes=5-'})
    result = downloads._Redirects().redirect_request(request, None, 302, '', {}, 'https://cas-bridge.xethub.hf.co/object?signature=x')
    assert result.get_header('Range') == 'bytes=5-'


def test_transfer_deadline(setup, monkeypatch):
    paths, _ = setup
    approve(paths)
    ticks = iter([0, 2])
    monkeypatch.setattr(downloads.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(TimeoutError):
        download_model(paths, opener=lambda *a, **k: Response(b'model data'), file_timeout=1)


@pytest.mark.parametrize('phase', ['headers', 'body'])
@pytest.mark.parametrize('interrupt', ['deadline', 'cancel'])
def test_slow_drip_transport_is_interruptible(setup, tmp_path, phase, interrupt):
    """Real sockets: occasional bytes defeat socket inactivity timeouts."""
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.request import urlopen
    paths, manifest = setup
    data = b'x' * (downloads.CHUNK + 1)
    manifest.update(inventory(data))
    approve(paths)
    stop, connected, cancel, done = (threading.Event() for _ in range(4))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                self.wfile.write(f'HTTP/1.1 200 OK\r\nContent-Length: {len(data)}\r\n'.encode())
                self.wfile.write(b'X-Slow: ' if phase == 'headers' else b'\r\n')
                self.wfile.flush()
                connected.set()
                while not stop.wait(.03):
                    self.wfile.write(b'x')
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    transport, children = child_transport(tmp_path, manifest,
        'def opener(request, timeout):\n'
        f'    return urlopen("http://127.0.0.1:{server.server_port}/fixture", timeout=timeout)\n')
    errors, events = [], []
    def run():
        try:
            downloads.download_model(paths, transport=transport,
                file_timeout=.5 if interrupt == 'deadline' else 10,
                cancelled=cancel.is_set, emit=events.append)
        except Exception as error:
            errors.append(error)
        finally:
            done.set()
    downloading = threading.Thread(target=run, daemon=True)
    downloading.start()
    try:
        assert connected.wait(3), 'test did not reach the real server'
        if interrupt == 'cancel':
            time.sleep(.12)  # ensure cancellation occurs inside open/read, not before it
            cancel.set()
        assert done.wait(1), 'transport remained blocked beyond deadline/cancellation bound'
        assert isinstance(errors[0], TimeoutError if interrupt == 'deadline' else downloads.DownloadCancelled), errors
        assert children[0].process.poll() is not None
        assert children[0].process.stdout.closed and children[0].process.stdin.closed
        assert events[-1].status == ('failed' if interrupt == 'deadline' else 'cancelled')
        assert not (downloads.snapshot_path(paths) / 'vae/weights.safetensors').exists()
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        serving.join(3)
        downloading.join(3)


@pytest.mark.parametrize('interrupt', ['deadline', 'cancel'])
def test_hung_establishment_substitute_is_reaped(setup, tmp_path, interrupt):
    import time
    paths, manifest = setup
    approve(paths)
    marker = tmp_path / 'establishing'
    transport, children = child_transport(tmp_path, manifest,
        'def opener(request, timeout):\n'
        '    import time\n'
        '    from pathlib import Path\n'
        f'    Path({str(marker)!r}).touch()\n'
        '    time.sleep(90)\n')
    def cancelled():
        # Trigger only after the child is inside the hung DNS/TLS/open substitute.
        return interrupt == 'cancel' and marker.exists()
    start = time.monotonic()
    with pytest.raises(TimeoutError if interrupt == 'deadline' else downloads.DownloadCancelled):
        downloads.download_model(paths, transport=transport, file_timeout=.5, cancelled=cancelled)
    assert marker.exists()
    assert time.monotonic() - start < 1.3
    assert children[0].process.poll() is not None
    assert children[0].process.stdin.closed and children[0].process.stdout.closed
    assert not (downloads.snapshot_path(paths) / 'vae/weights.safetensors').exists()


def scripted_transport(tmp_path, source):
    import sys
    from h3server._download_transport import DownloadTransport
    script = tmp_path / 'broken_transport.py'
    script.write_text('import sys, os, time, signal\n' + source)
    children = []
    def factory(*args, **kwargs):
        response = DownloadTransport(*args, **kwargs, command=(sys.executable, str(script)))
        children.append(response)
        return response
    return factory, children


@pytest.mark.parametrize('source', [
    'sys.exit(7)',
    'sys.exit(0)',  # clean EOF without a response is not a completed download
    'sys.stdout.write(\'{}\\n\'); sys.stdout.flush()',
    'sys.stdout.write(\'x\' * 17000); sys.stdout.flush(); time.sleep(90)',
    'sys.stdout.buffer.write(b\'{"status":200,"headers":{"Content-Length":"10"}}\\nmodel data\'); sys.stdout.flush(); sys.exit(7)',
    'sys.stdout.buffer.write(b\'{"status":200,"headers":{"Content-Length":"10"}}\\nmodel\'); sys.stdout.flush()',
])
def test_crash_eof_malformed_or_oversized_ipc_never_publishes(setup, tmp_path, source):
    paths, _ = setup
    approve(paths)
    transport, children = scripted_transport(tmp_path, source)
    with pytest.raises(ValueError):
        downloads.download_model(paths, transport=transport, file_timeout=3)
    assert children[0].process.poll() is not None
    assert children[0].process.stdin.closed and children[0].process.stdout.closed
    assert not (downloads.snapshot_path(paths) / 'vae/weights.safetensors').exists()


def test_closed_stdout_but_live_child_obeys_deadline(setup, tmp_path):
    paths, _ = setup
    approve(paths)
    transport, children = scripted_transport(tmp_path,
        'sys.stdout.buffer.write(b\'{"status":200,"headers":{"Content-Length":"10"}}\\nmodel data\'); '
        'sys.stdout.flush(); os.close(1); time.sleep(90)')
    with pytest.raises(TimeoutError):
        downloads.download_model(paths, transport=transport, file_timeout=.4)
    assert children[0].process.poll() is not None
    assert not (downloads.snapshot_path(paths) / 'vae/weights.safetensors').exists()


def test_terminate_resistant_transport_is_killed_then_reaped(setup, tmp_path):
    import signal
    import time
    paths, _ = setup
    approve(paths)
    marker = tmp_path / 'ignoring-term'
    transport, children = scripted_transport(tmp_path,
        f'signal.signal(signal.SIGTERM, signal.SIG_IGN)\nopen({str(marker)!r}, "w").close()\ntime.sleep(90)')
    start = time.monotonic()
    with pytest.raises(downloads.DownloadCancelled):
        downloads.download_model(paths, transport=transport, cancelled=marker.exists, file_timeout=3)
    assert time.monotonic() - start < 1.5
    assert children[0].process.returncode == -signal.SIGKILL
    assert children[0].process.stdin.closed and children[0].process.stdout.closed


def test_cancellation_before_startup_never_launches_child(setup):
    paths, _ = setup
    approve(paths)
    with pytest.raises(downloads.DownloadCancelled):
        downloads.download_model(paths, cancelled=lambda: True,
            transport=lambda *a, **k: pytest.fail('cancelled before process startup'))


def test_real_streamed_range_resume_integrity_and_progress(setup, tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    paths, manifest = setup
    data = b'abcdef' * 50000
    manifest.update(inventory(data))
    approve(paths)
    partial = paths.tmp_downloads / manifest['model']['revision'] / 'vae/weights.safetensors.part'
    partial.parent.mkdir(parents=True)
    partial.write_bytes(data[:19])
    ranges = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            ranges.append(self.headers.get('Range'))
            self.send_response(206)
            self.send_header('Content-Length', str(len(data)-19))
            self.send_header('Content-Range', f'bytes 19-{len(data)-1}/{len(data)}')
            self.end_headers()
            for offset in range(19,len(data),8192):
                self.wfile.write(data[offset:offset+8192])
                self.wfile.flush()
        def log_message(self,*args): pass
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    serving = threading.Thread(target=server.serve_forever,daemon=True)
    serving.start()
    transport, children = child_transport(tmp_path, manifest,
        'def opener(request, timeout):\n'
        f'    return urlopen(Request("http://127.0.0.1:{server.server_port}/fixture", '
        'headers=dict(request.header_items())), timeout=timeout)\n')
    events = []
    try:
        snapshot = downloads.download_model(paths, transport=transport, emit=events.append, file_timeout=3)
        assert (snapshot / 'vae/weights.safetensors').read_bytes() == data
        assert ranges == ['bytes=19-']
        assert events[-1].status == 'completed' and events[-1].progress.completed == len(data)
        assert all(e.progress.completed <= len(data) for e in events)
        assert children[0].process.returncode == 0
        assert children[0].process.stdin.closed and children[0].process.stdout.closed
        assert not partial.exists()
    finally:
        server.shutdown()
        server.server_close()
        serving.join(3)


@pytest.mark.parametrize('reason', ['parent_pipe_closed', 'consumer_stalled'])
def test_child_watchdog_exits_without_parent_consuming_output(setup, tmp_path, reason):
    """The production child watchdog bounds backpressure, not just parent read()."""
    import os
    import subprocess
    import sys
    import time
    paths, manifest = setup
    manifest.update(inventory(b'x' * (downloads.CHUNK * 3)))
    approve(paths)
    # No HTTP request: a boundary response floods stdout to fill the OS pipe.
    child_transport(tmp_path, manifest,
        'def opener(request, timeout):\n'
        '    import io\n'
        '    from email.message import Message\n'
        '    response = io.BytesIO(b"x" * (1024 * 1024 * 3))\n'
        '    response.status = 200\n'
        '    response.headers = Message()\n'
        '    response.headers["Content-Length"] = str(1024 * 1024 * 3)\n'
        '    return response\n')
    deadline = time.monotonic() + 1.5
    with subprocess.Popen([sys.executable,str(tmp_path/'transport_fixture.py')],
                          stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                          env={**os.environ, **paths.cache_environment(), 'HOME':str(paths.root)}) as child:
        try:
            document = {'name':'vae/weights.safetensors','offset':0,'revision':manifest['model']['revision'],
                        'timeout':30,'deadline':deadline}
            child.stdin.write(json.dumps(document).encode() + b'\n')
            child.stdin.flush()
            # Wait for metadata only; do not consume the multi-MiB body.
            assert child.stdout.readline().startswith(b'{"status": 200')
            if reason == 'parent_pipe_closed':
                child.stdin.close()  # same EOF delivered by OS when parent dies
            assert child.wait(timeout=2) != 0
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


@pytest.mark.parametrize('changed', [{'name':'../escape'}, {'revision':'main'}, {'url':'https://evil.example'},
                                     {'offset':-1}, {'timeout':float('inf')}])
def test_child_only_accepts_validated_pinned_request(setup, changed):
    import time
    from h3server._download_transport import _fetch
    _, manifest = setup
    document = {'name':'vae/weights.safetensors','offset':0,'revision':manifest['model']['revision'],
                'timeout':30,'deadline':time.monotonic()+10, **changed}
    with pytest.raises(ValueError):
        _fetch(document, opener=lambda *a, **k: pytest.fail('invalid request reached network'))


@pytest.mark.parametrize('headers', ['duplicate', 'oversized'])
def test_child_rejects_ambiguous_or_oversized_http_headers(setup, tmp_path, headers):
    paths, manifest = setup
    approve(paths)
    extra = ('    response.headers["Content-Length"] = "10"\n' if headers == 'duplicate'
             else '    response.headers["X-Oversized"] = "x" * 17000\n')
    transport, children = child_transport(tmp_path, manifest,
        'def opener(request, timeout):\n'
        '    import io\n'
        '    from email.message import Message\n'
        '    response = io.BytesIO(b"model data")\n'
        '    response.status = 200\n'
        '    response.headers = Message()\n'
        '    response.headers["Content-Length"] = "10"\n' + extra +
        '    return response\n')
    with pytest.raises(ValueError):
        downloads.download_model(paths, transport=transport, file_timeout=3)
    assert children[0].process.returncode != 0
    assert not (downloads.snapshot_path(paths) / 'vae/weights.safetensors').exists()


def test_internal_transport_entry_point_rejects_oversized_request_without_network():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-m', 'h3server._download_transport'],
        input=b'x' * 5000 + b'\n', capture_output=True, timeout=3)
    assert result.returncode != 0 and result.stdout == b''


def test_direct_transport_invocation_still_requires_persisted_consent(setup, tmp_path):
    import time
    paths, manifest = setup
    marker = tmp_path/'network-opened'
    transport, children = child_transport(tmp_path, manifest,
        'def opener(request, timeout):\n'
        '    from pathlib import Path\n'
        f'    Path({str(marker)!r}).touch()\n'
        '    raise AssertionError("network must not be called")\n')
    with pytest.raises(ValueError):
        with transport(paths, 'vae/weights.safetensors', 0, timeout=30,
                       deadline=time.monotonic()+3, cancelled=lambda:False):
            pytest.fail('transport bypassed consent')
    assert not marker.exists()
    assert children[0].process.returncode != 0
