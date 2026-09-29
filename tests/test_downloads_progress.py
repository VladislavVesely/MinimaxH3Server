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
    return paths, manifest


def approve(paths):
    downloads.record_consent(paths, license_sha256="b" * 64, license_acknowledged=True,
                             eligibility_confirmed=True, download_approved=True)


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {"Content-Length": str(len(data))}


@pytest.mark.parametrize("lfs", [True, False])
def test_download_hash_progress_and_atomic_promotion(setup, lfs):
    paths, manifest = setup
    manifest.update(inventory(lfs=lfs))
    approve(paths)
    events, calls = [], []
    def fetch(request, timeout):
        calls.append(request)
        return Response(b"model data")
    snapshot = downloads.download_model(paths, emit=events.append, opener=fetch)
    assert (snapshot / "vae/weights.safetensors").read_bytes() == b"model data"
    assert events[-1].status == "completed"
    assert events[-1].progress.completed == 10
    assert calls[0].full_url.endswith("a" * 40 + "/vae/weights.safetensors")
    assert downloads.verify_snapshot(paths) == snapshot
    downloads.download_model(paths, opener=lambda *a, **k: pytest.fail("already verified"))


def test_consent_required_before_any_network(setup):
    paths, _ = setup
    with pytest.raises(ValueError, match="consent"):
        downloads.download_model(paths, opener=lambda *a, **k: pytest.fail("network before consent"))
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
        downloads.download_model(paths, opener=lambda *a, **k: Response(data, headers=headers))
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
        downloads.download_model(paths, opener=lambda *a, **k: Interrupted(b"model data"),
                                 cancelled=lambda: cancelled[0])
    calls = []
    def resume(request, timeout):
        calls.append(request)
        return Response(b" data", 206, {"Content-Length": "5", "Content-Range": "bytes 5-9/10"})
    downloads.download_model(paths, opener=resume)
    assert calls[0].get_header("Range") == "bytes=5-"


@pytest.mark.parametrize("status,content_range", [(206, "bytes 4-9/10"), (206, "bytes 5-9/11"), (416, "bytes */10")])
def test_bad_resume_range_rejected(setup, status, content_range):
    paths, _ = setup
    approve(paths)
    with pytest.raises(ValueError):
        downloads.download_model(paths, opener=lambda *a, **k: Response(b"model"))
    with pytest.raises(ValueError):
        downloads.download_model(paths, opener=lambda *a, **k: Response(b" data", status, {"Content-Range": content_range}))


def test_server_ignoring_range_restarts_instead_of_appending(setup):
    paths, _ = setup
    approve(paths)
    with pytest.raises(ValueError):
        downloads.download_model(paths, opener=lambda *a, **k: Response(b"model"))
    downloads.download_model(paths, opener=lambda *a, **k: Response(b"model data"))
    assert downloads.verify_snapshot(paths)


@pytest.mark.parametrize("name", ["../escape", "/escape", "x/../../escape", "x\\escape", "model.bin"])
def test_manifest_paths_and_pickle_fail_closed(setup, name):
    paths, manifest = setup
    manifest["model"]["files"][0]["rfilename"] = name
    approve(paths)
    with pytest.raises(ValueError):
        downloads.download_model(paths, opener=lambda *a, **k: pytest.fail("unsafe inventory"))


def test_symlink_and_hardlink_refused(setup, tmp_path):
    paths, _ = setup
    approve(paths)
    outside = tmp_path / "outside"
    outside.mkdir()
    snapshot = downloads.snapshot_path(paths)
    snapshot.parent.mkdir(exist_ok=True)
    snapshot.symlink_to(outside, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        downloads.download_model(paths, opener=lambda *a, **k: pytest.fail("symlink"))


def test_extra_pickle_invalidates_snapshot(setup):
    paths, _ = setup
    approve(paths)
    snapshot = downloads.download_model(paths, opener=lambda *a, **k: Response(b"model data"))
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
        downloads.download_model(paths, opener=lambda *a, **k: Response(b' data', 206, {'Content-Range':'bytes 5-9/10'}))
    assert partial.stat().st_size == 0
    downloads.download_model(paths, opener=lambda *a, **k: Response(b'model data'))


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
        downloads.download_model(paths, opener=lambda *a, **k: pytest.fail('hardlink'))
    assert outside.read_bytes() == b'keep'


def test_hashing_is_cooperatively_cancellable(setup):
    paths, _ = setup
    approve(paths)
    downloads.download_model(paths, opener=lambda *a, **k: Response(b'model data'))
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
        downloads.download_model(paths, opener=lambda *a, **k: Response(b'model data'), file_timeout=1)
