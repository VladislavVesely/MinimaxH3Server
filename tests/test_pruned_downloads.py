"""Controlled bytes/HTTP boundaries only; no model weights or GPU imports."""
from dataclasses import replace
import io
import time
from types import SimpleNamespace

import pytest

from h3server import backends, downloads
from h3server._download_transport import DownloadTransport, _fetch
from test_pruned_catalog import consent, tiny_catalog  # noqa: F401 (shared fixture)
from test_downloads_progress import Response


def test_pruned_resume_after_cancellation_and_progress(tiny_catalog):
    paths, selected, contents = tiny_catalog
    consent(paths, selected)
    stopped = [False]
    class Interrupted(Response):
        def read(self, size=-1):
            result = super().read(min(size, 5))
            stopped[0] = True
            return result
    with pytest.raises(downloads.DownloadCancelled):
        downloads.download_model(paths, profile_name=selected.profile_name,
            cancelled=lambda: stopped[0], transport=lambda paths, name, offset, **kw: Interrupted(contents[name]))
    assert not downloads.snapshot_path(paths, profile_name=selected.profile_name).exists()
    offsets, events = [], []
    def resume(paths, name, offset, **kw):
        offsets.append(offset)
        data = contents[name]
        return Response(data[offset:], status=206 if offset else 200,
            headers={"Content-Length": str(len(data) - offset),
                     "Content-Range": f"bytes {offset}-{len(data)-1}/{len(data)}"})
    downloads.download_model(paths, profile_name=selected.profile_name, transport=resume, emit=events.append)
    assert offsets == [5, 0, 0, 0]
    assert events[-1].progress.completed == selected.weight_bytes
    assert events[-1].progress.total == selected.weight_bytes
    assert not list((paths.root / "tmp/downloads").rglob("*.part"))


@pytest.mark.parametrize("kind", ["wrong-hash", "symlink", "extra-file"])
def test_pruned_download_fail_closed(tiny_catalog, kind):
    paths, selected, contents = tiny_catalog
    consent(paths, selected)
    first = selected.artifacts[0]
    if kind == "wrong-hash":
        with pytest.raises(ValueError, match="integrity"):
            downloads.download_model(paths, profile_name=selected.profile_name,
                transport=lambda paths, name, offset, **kw: Response(b"x" * len(contents[name])))
        assert not downloads.snapshot_path(paths, profile_name=selected.profile_name).exists()
        return
    snapshot = downloads.download_model(paths, profile_name=selected.profile_name,
        transport=lambda paths, name, offset, **kw: Response(contents[name]))
    if kind == "extra-file":
        (snapshot / "unexpected.gguf").write_bytes(b"not selected")
    else:
        target = snapshot / first.local_relative_path
        target.unlink()
        outside = paths.root / "outside"
        outside.write_bytes(contents[first.local_relative_path])
        target.symlink_to(outside)
    with pytest.raises(ValueError):
        downloads.verify_snapshot(paths, profile_name=selected.profile_name)


def test_changed_auxiliary_contract_invalidates_consent(tiny_catalog, monkeypatch):
    paths, selected, _ = tiny_catalog
    consent(paths, selected)
    changed = replace(selected, artifacts=(selected.artifacts[0],
        replace(selected.artifacts[1], sha256="f" * 64), *selected.artifacts[2:]))
    monkeypatch.setattr(backends, "get_artifact_set", lambda name: changed)
    with pytest.raises(ValueError, match="consent"):
        downloads.require_consent(paths, profile_name=selected.profile_name)


def test_transport_resolves_each_artifacts_own_repo_and_pin(tiny_catalog, monkeypatch):
    paths, selected, contents = tiny_catalog
    consent(paths, selected)
    calls = []
    class HTTPResponse(Response):
        def __init__(self, data):
            from email.message import Message
            super().__init__(data)
            self.headers = Message()
            self.headers["Content-Length"] = str(len(data))
        read1 = io.BytesIO.read
    monkeypatch.setenv("HOME", str(paths.root))
    # The watchdog supervises actual child processes elsewhere in the regression
    # suite. Here only its start is substituted to safely exercise _fetch in-process.
    monkeypatch.setattr("h3server._download_transport.threading.Thread",
                        lambda **kw: SimpleNamespace(start=lambda: None))
    for artifact in selected.artifacts:
        transport = DownloadTransport(paths, artifact.local_relative_path, 0, timeout=1,
            deadline=time.monotonic() + 5, cancelled=lambda: False, profile_name=selected.profile_name)
        assert transport.document["artifact_set_digest"] == selected.digest
        def opener(request, timeout):
            calls.append(request.full_url)
            return HTTPResponse(contents[artifact.local_relative_path])
        output = io.BytesIO()
        with monkeypatch.context() as patch:
            patch.setattr("h3server._download_transport.sys.stdout", SimpleNamespace(buffer=output))
            _fetch(transport.document, opener=opener)
        assert output.getvalue().endswith(contents[artifact.local_relative_path])
    assert calls == [a.url for a in selected.artifacts]
    assert len({a.repository for a in selected.artifacts}) == 3


@pytest.mark.parametrize("mutation", ["legacy", "other-variant", "digest", "profile", "arbitrary-url"])
def test_transport_rejects_cross_selection_before_network(tiny_catalog, mutation):
    paths, selected, _ = tiny_catalog
    consent(paths, selected)
    doc = DownloadTransport(paths, selected.artifacts[0].local_relative_path, 0, timeout=1,
        deadline=time.monotonic() + 5, cancelled=lambda: False, profile_name=selected.profile_name).document
    if mutation == "legacy":
        doc["name"] = "transformer/diffusion_pytorch_model-00001-of-00008.safetensors"
    elif mutation == "other-variant":
        doc["name"] = backends.get_artifact_set("h3-gguf-q5-k-m").artifacts[0].local_relative_path
    elif mutation == "digest":
        doc["artifact_set_digest"] = "0" * 64
    elif mutation == "profile":
        doc["profile_name"] = "unknown-profile"
    else:
        doc["url"] = "https://example.org/weights"
    with pytest.raises(ValueError):
        _fetch(doc, opener=lambda *a, **kw: pytest.fail("unapproved network"))
