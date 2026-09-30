"""Pinned model acquisition, never an arbitrary-URL downloader.

Integration for setup: present the complete pinned LICENSE/Exhibit A (the manifest
source URL and SHA256 identify it), then call record_consent with explicit license,
eligibility and model-download approval. Installation consent is NOT reused.
record_consent is target-side persisted consent; download_model resumes exactly
that inventory and emits domain Events for Store.append_event('setup', id, event).
No downloader executes Python from a model or accepts pickle weights. All staging
is tmp/downloads/<revision-or-set-digest>; the corresponding models/h3-* snapshot
must be verified before each worker (explicit profile_name for pruned weights).
Cancellation leaves partial bytes resumable, never marked ready. Final hashes,
not ETags (which can describe LFS pointers), bind resumed bytes to the pin.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler

from h3server._download_transport import CHUNK, DownloadTransport

from h3server.backends import load_manifest
from h3server.backends.base import atomic_json, directory, parts, read_json, regular_file
from h3server.domain import Event, Progress, StageStatus
from h3server.paths import AppPaths


class DownloadCancelled(RuntimeError):
    pass


def _selected_manifest(profile_name=None):
    """Private bridge to the legacy integrity loop; never a caller-supplied manifest."""
    manifest = load_manifest()
    if profile_name is None or profile_name in {p["name"] for p in manifest.get("profiles", ())}:
        return manifest
    from h3server.backends import get_artifact_set
    selected = get_artifact_set(profile_name)
    entries = [{"rfilename": a.local_relative_path, "size": a.size_bytes,
                "lfs": {"sha256": a.sha256, "size": a.size_bytes},
                "repository": a.repository, "revision": a.revision, "source_path": a.path}
               for a in selected.artifacts]
    return {"profile_name": profile_name, "artifact_set_digest": selected.digest,
            "model": {"repository": selected.artifacts[0].repository,
                      "revision": selected.artifacts[0].revision, "files": entries,
                      "total_bytes": selected.weight_bytes}, "sources": manifest["sources"]}


def _binding(manifest):
    model = manifest["model"]
    binding = {"schema_version": 1, "repository": model["repository"], "revision": model["revision"],
               "inventory_sha256": hashlib.sha256(json.dumps(model["files"], sort_keys=True).encode()).hexdigest(),
               "license_sha256": manifest["sources"]["license"]["sha256"]}
    if "artifact_set_digest" in manifest:
        binding.update(schema_version=2, profile_name=manifest["profile_name"],
                       artifact_set_digest=manifest["artifact_set_digest"])
    return binding


def _consent_path(manifest):
    if "profile_name" in manifest:
        return f"config/h3-consent-{manifest['profile_name']}.json"
    return "config/h3-consent.json"


def _snapshot_id(manifest):
    return manifest.get("artifact_set_digest", manifest["model"]["revision"])


def record_consent(paths, *, license_sha256, license_acknowledged, eligibility_confirmed, download_approved,
                   profile_name=None, artifact_set_digest=None):
    """Caller must present full license; eligibility may require separate authorization.

    Pruned callers must also present ArtifactSet.license_disclosures and bind the
    displayed selected set via artifact_set_digest. Runtime tokenizers are part
    of this approval contract but are provisioned/verified by the runtime owner.
    A boolean is an operator attestation, not legal certification. Never infer it
    from territory, public Hub availability or general sudo/installation approval.
    """
    manifest = _selected_manifest(profile_name)
    binding = _binding(manifest)
    if "artifact_set_digest" in manifest and artifact_set_digest != manifest["artifact_set_digest"]:
        raise ValueError("explicit selected artifact set approval required")
    if license_sha256 != binding["license_sha256"] or any(v is not True for v in (
        license_acknowledged, eligibility_confirmed, download_approved
    )):
        raise ValueError("explicit pinned license, eligibility and download consent required")
    atomic_json(paths, _consent_path(manifest), {**binding, "license_acknowledged": True,
                "eligibility_confirmed": True, "download_approved": True})


def require_consent(paths, *, profile_name=None):
    manifest = _selected_manifest(profile_name)
    expected = {**_binding(manifest), "license_acknowledged": True,
                "eligibility_confirmed": True, "download_approved": True}
    try:
        consent = read_json(paths, _consent_path(manifest))
        if consent != expected or any(consent.get(k) is not True for k in (
            "license_acknowledged", "eligibility_confirmed", "download_approved"
        )):
            raise ValueError("stale consent")
    except (OSError, ValueError) as exc:
        raise ValueError("persisted model/license/eligibility consent required") from exc


def _inventory(manifest):
    if "profile_name" in manifest:
        if manifest != _selected_manifest(manifest["profile_name"]):
            raise ValueError("unexpected selected artifact inventory")
        return manifest["model"]["files"]
    model = manifest["model"]
    if model["repository"] != "MiniMaxAI/MiniMax-H3" or not re.fullmatch(r"[a-f0-9]{40}", model["revision"]):
        raise ValueError("unexpected model identity")
    names = set()
    for entry in model["files"]:
        name = entry["rfilename"]
        parts(name)
        if name in names or Path(name).suffix not in {".json", ".safetensors", ".txt", ""}:
            raise ValueError("unsupported model artifact")
        names.add(name)
        if type(entry["size"]) is not int or not 0 < entry["size"] <= 16 * 1024**3:
            raise ValueError("invalid artifact size")
        lfs = entry.get("lfs")
        digest = lfs["sha256"] if lfs else entry["blobId"]
        if not re.fullmatch(r"[a-f0-9]{" + ("64" if lfs else "40") + r"}", digest):
            raise ValueError("invalid pinned digest")
        if lfs and lfs["size"] != entry["size"]:
            raise ValueError("inconsistent LFS size")
    if not names or sum(e["size"] for e in model["files"]) != model["total_bytes"]:
        raise ValueError("inconsistent inventory size")
    return model["files"]


def snapshot_path(paths, *, profile_name=None):
    manifest = _selected_manifest(profile_name)
    _inventory(manifest)
    # Retain spelling: descriptor walks, not Path.resolve(), reject aliases.
    return paths.root / "models" / ("h3-" + _snapshot_id(manifest))


def _verify(stream, entry, cancelled=lambda: False):
    before = os.fstat(stream.fileno())
    if before.st_size != entry["size"]:
        raise ValueError("artifact size mismatch")
    digest = hashlib.sha256() if "lfs" in entry else hashlib.sha1()
    if "lfs" not in entry:
        digest.update(b"blob " + str(entry["size"]).encode("ascii") + b"\0")
    stream.seek(0)
    remaining = entry["size"]
    while remaining:
        if cancelled():
            raise DownloadCancelled("download cancelled during integrity check")
        chunk = stream.read(min(CHUNK, remaining))
        if not chunk:
            raise ValueError("artifact truncated during integrity check")
        digest.update(chunk)
        remaining -= len(chunk)
    if stream.read(1):
        raise ValueError("artifact grew during integrity check")
    after = os.fstat(stream.fileno())
    expected = entry["lfs"]["sha256"] if "lfs" in entry else entry["blobId"]
    if digest.hexdigest() != expected or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size, after.st_mtime_ns, after.st_ctime_ns
    ):
        raise ValueError("artifact integrity mismatch")


def _names(fd, prefix=""):
    result = set()
    for name in os.listdir(fd):
        # Open directories without following symlinks; files get verified separately.
        import stat
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                result.update(_names(child, prefix + name + "/"))
            finally:
                os.close(child)
        elif stat.S_ISREG(info.st_mode):
            result.add(prefix + name)
        else:
            raise ValueError("snapshot contains nonregular artifact")
    return result


def verify_snapshot(paths, *, cancelled=lambda: False, profile_name=None):
    manifest = _selected_manifest(profile_name)
    entries = _inventory(manifest)
    snapshot = snapshot_path(paths, profile_name=profile_name)
    relative = str(snapshot.relative_to(paths.root))
    with directory(paths, relative) as fd:
        if _names(fd) != {entry["rfilename"] for entry in entries}:
            raise ValueError("snapshot inventory mismatch")
    for entry in entries:
        with regular_file(paths, relative + "/" + entry["rfilename"]) as stream:
            _verify(stream, entry, cancelled)
    return snapshot


class _Redirects(HTTPRedirectHandler):
    """Hub's signed CDN redirect is allowed, never credentials/HTTP/other hosts."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not any(host == suffix or host.endswith("." + suffix) for suffix in ("huggingface.co", "hf.co", "xethub.hf.co"))):
            raise ValueError("untrusted model redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@contextmanager
def _lock(paths):
    import fcntl
    with regular_file(paths, "state/model-download.lock", flags=os.O_CREAT | os.O_RDWR,
                      mode="r+b", create_parents=True) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def _shared_candidates(profile_name, entry):
    from h3server.backends.catalog import PRUNED_PROFILE_NAMES
    if profile_name not in PRUNED_PROFILE_NAMES:return []
    candidates=[]
    for profile in PRUNED_PROFILE_NAMES:
        if profile==profile_name:continue
        manifest=_selected_manifest(profile)
        for other in _inventory(manifest):
            if other['size']==entry['size'] and other.get('lfs')==entry.get('lfs') and entry.get('lfs'):
                candidates.append('models/h3-'+_snapshot_id(manifest)+'/'+other['rfilename'])
    return candidates


def _reuse_shared(paths,entry,candidates,target,cancelled):
    """Verified private reflink/copy, never a hardlink or writable source alias."""
    for source in candidates:
        try:
            with regular_file(paths,source) as stream:
                try:_verify(stream,entry,cancelled)
                except ValueError:continue
                target.seek(0);target.truncate(0)
                try:
                    import fcntl
                    fcntl.ioctl(target.fileno(),0x40049409,stream.fileno()) # Linux FICLONE
                except OSError:
                    stream.seek(0);target.seek(0);target.truncate(0)
                    while chunk:=stream.read(CHUNK):
                        if cancelled():raise DownloadCancelled('cancelled shared-file copy')
                        target.write(chunk)
                target.flush();os.fsync(target.fileno())
                _verify(target,entry,cancelled)
                return True
        except FileNotFoundError:continue
    return False


def download_model(paths, *, emit=lambda event: None, cancelled=lambda: False, transport=None,
                   timeout=30, file_timeout=6 * 3600, profile_name=None):
    """Explicit injectable IO boundary; default uses bounded HTTPS, no auth tokens.

    One response per artifact per call, <=30s socket inactivity wait and <=6h
    absolute transport deadline, including child startup, DNS, TLS and headers.
    Production transport polls cancellation every 50ms and terminates/reaps its
    IO child on failure (200ms terminate grace, then kill). Only the parent writes
    files. Reinvoke to resume; no implicit retries or downloads on model load.
    transport is a trusted Python response-context factory for boundary tests,
    not an arbitrary-URL API. Its arguments are paths, pinned name, offset and
    keyword timeout/deadline/cancelled; blocking IO belongs in its supervised child.
    """
    if not 0 < timeout <= 30 or not 0 < file_timeout <= 6 * 3600:
        raise ValueError("invalid download time limit")
    require_consent(paths, profile_name=profile_name)
    manifest = _selected_manifest(profile_name)
    entries = _inventory(manifest)
    revision = _snapshot_id(manifest)
    destination = f"models/h3-{revision}"
    staging = f"tmp/downloads/{revision}"
    transport = transport or DownloadTransport
    completed, total = 0, manifest["model"]["total_bytes"]
    def event(status):
        emit(Event("model_download", status, progress=Progress(completed, "bytes", total)))
    def check_cancel():
        if cancelled():
            raise DownloadCancelled("download cancelled")
    with _lock(paths):
        event(StageStatus.RUNNING)
        try:
            for entry in entries:
                check_cancel()
                require_consent(paths, profile_name=profile_name)
                name = entry["rfilename"]
                target, partial = destination + "/" + name, staging + "/" + name + ".part"
                try:
                    with regular_file(paths, target) as stream:
                        _verify(stream, entry, cancelled)
                except FileNotFoundError:
                    pass
                else:
                    completed += entry["size"]
                    event(StageStatus.RUNNING)
                    continue
                with regular_file(paths, partial, flags=os.O_CREAT | os.O_RDWR,
                                  mode="r+b", create_parents=True) as stream:
                    offset = os.fstat(stream.fileno()).st_size
                    if offset == 0:
                        if _reuse_shared(paths,entry,_shared_candidates(profile_name,entry),stream,cancelled):
                            offset=entry['size']
                    if offset > entry["size"]:
                        stream.truncate(0)
                        offset = 0
                    if offset < entry["size"]:
                        deadline = time.monotonic() + file_timeout
                        selection = {"profile_name": profile_name} if "profile_name" in manifest else {}
                        with transport(paths, name, offset, timeout=timeout, deadline=deadline,
                                       cancelled=cancelled, **selection) as response:
                            status = response.status
                            if status == 200:
                                stream.seek(0)
                                stream.truncate(0)
                                offset = 0
                            elif status == 206 and offset:
                                if response.headers.get("Content-Range") != f"bytes {offset}-{entry['size']-1}/{entry['size']}":
                                    raise ValueError("invalid resume range")
                            else:
                                raise ValueError("unexpected download response")
                            remaining = entry["size"] - offset
                            length = response.headers.get("Content-Length")
                            if (length is not None and length != str(remaining)) or response.headers.get("Content-Encoding", "identity") != "identity":
                                raise ValueError("unexpected download size or encoding")
                            completed += offset
                            stream.seek(offset)
                            while True:
                                check_cancel()
                                if time.monotonic() > deadline:
                                    raise TimeoutError("artifact transfer deadline")
                                chunk = response.read(min(CHUNK, remaining + 1))
                                if not chunk:
                                    break
                                if len(chunk) > remaining:
                                    raise ValueError("download exceeds pinned size")
                                stream.write(chunk)
                                remaining -= len(chunk)
                                completed += len(chunk)
                                event(StageStatus.RUNNING)
                            if remaining:
                                raise ValueError("truncated download")
                    else:
                        completed += offset
                    stream.flush()
                    os.fsync(stream.fileno())
                    check_cancel()
                    try:
                        _verify(stream, entry, cancelled)
                    except ValueError:
                        # Do not get stuck retrying a complete but corrupt partial.
                        stream.truncate(0)
                        raise
                with directory(paths, str(Path(partial).parent)) as source_fd, directory(
                    paths, str(Path(target).parent), create=True
                ) as target_fd:
                    os.replace(Path(partial).name, Path(target).name, src_dir_fd=source_fd, dst_dir_fd=target_fd)
                    os.fsync(target_fd)
            check_cancel()
            verify_snapshot(paths, cancelled=cancelled, profile_name=profile_name)
        except DownloadCancelled:
            event(StageStatus.CANCELLED)
            raise
        except BaseException:
            event(StageStatus.FAILED)
            raise
        event(StageStatus.COMPLETED)
    return snapshot_path(paths, profile_name=profile_name)


def main(argv=None):
    """Acquisition only; consent is recorded by the setup owner, not an env flag."""
    import argparse
    from h3server.events import encode_event
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--profile", default=None, help="Exact catalog profile; default preserves legacy behavior")
    args = parser.parse_args(argv)
    try:
        download_model(AppPaths(args.root), profile_name=args.profile,
                       emit=lambda event: print(encode_event(event), flush=True))
    except Exception:
        raise SystemExit("model download failed; consent, storage, network or integrity check did not pass") from None


if __name__ == "__main__":
    main()
