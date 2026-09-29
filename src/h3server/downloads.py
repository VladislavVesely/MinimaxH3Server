"""Pinned model acquisition, never an arbitrary-URL downloader.

Integration for setup: present the complete pinned LICENSE/Exhibit A (the manifest
source URL and SHA256 identify it), then call record_consent with explicit license,
eligibility and model-download approval. Installation consent is NOT reused.
record_consent is target-side persisted consent; download_model resumes exactly
that inventory and emits domain Events for Store.append_event('setup', id, event).
No downloader executes Python from a model or accepts pickle weights. All staging
is tmp/downloads/<revision>; models/h3-<revision> is verified before each worker.
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
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from h3server.backends import load_manifest
from h3server.backends.base import atomic_json, directory, parts, read_json, regular_file
from h3server.domain import Event, Progress, StageStatus
from h3server.paths import AppPaths

CHUNK = 1024 * 1024


class DownloadCancelled(RuntimeError):
    pass


def _binding(manifest):
    model = manifest["model"]
    return {"schema_version": 1, "repository": model["repository"], "revision": model["revision"],
            "inventory_sha256": hashlib.sha256(json.dumps(model["files"], sort_keys=True).encode()).hexdigest(),
            "license_sha256": manifest["sources"]["license"]["sha256"]}


def record_consent(paths, *, license_sha256, license_acknowledged, eligibility_confirmed, download_approved):
    """Caller must present full license; eligibility may require separate authorization.

    A boolean is an operator attestation, not legal certification. Never infer it
    from territory, public Hub availability or general sudo/installation approval.
    """
    binding = _binding(load_manifest())
    if license_sha256 != binding["license_sha256"] or any(v is not True for v in (
        license_acknowledged, eligibility_confirmed, download_approved
    )):
        raise ValueError("explicit pinned license, eligibility and download consent required")
    atomic_json(paths, "config/h3-consent.json", {**binding, "license_acknowledged": True,
                "eligibility_confirmed": True, "download_approved": True})


def require_consent(paths):
    expected = {**_binding(load_manifest()), "license_acknowledged": True,
                "eligibility_confirmed": True, "download_approved": True}
    try:
        consent = read_json(paths, "config/h3-consent.json")
        if consent != expected or any(consent.get(k) is not True for k in (
            "license_acknowledged", "eligibility_confirmed", "download_approved"
        )):
            raise ValueError("stale consent")
    except (OSError, ValueError) as exc:
        raise ValueError("persisted model/license/eligibility consent required") from exc


def _inventory(manifest):
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


def snapshot_path(paths):
    manifest = load_manifest()
    _inventory(manifest)
    # Retain spelling: descriptor walks, not Path.resolve(), reject aliases.
    return paths.root / "models" / ("h3-" + manifest["model"]["revision"])


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


def verify_snapshot(paths, *, cancelled=lambda: False):
    manifest = load_manifest()
    entries = _inventory(manifest)
    snapshot = snapshot_path(paths)
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


def download_model(paths, *, emit=lambda event: None, cancelled=lambda: False, opener=None,
                   timeout=30, file_timeout=6 * 3600):
    """Explicit injectable IO boundary; default uses bounded HTTPS, no auth tokens.

    One response per artifact per call, <=30s socket wait, <=6h per file. Reinvoke
    after transport failure to resume. No implicit retry loop or download on load.
    """
    if not 0 < timeout <= 30 or not 0 < file_timeout <= 6 * 3600:
        raise ValueError("invalid download time limit")
    require_consent(paths)
    manifest = load_manifest()
    entries = _inventory(manifest)
    revision = manifest["model"]["revision"]
    destination = f"models/h3-{revision}"
    staging = f"tmp/downloads/{revision}"
    opener = opener or build_opener(_Redirects()).open
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
                require_consent(paths)
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
                    if offset > entry["size"]:
                        stream.truncate(0)
                        offset = 0
                    if offset < entry["size"]:
                        headers = {"Accept-Encoding": "identity", "User-Agent": "h3server-pinned-downloader/1"}
                        if offset:
                            headers["Range"] = f"bytes={offset}-"
                        url = f"https://huggingface.co/MiniMaxAI/MiniMax-H3/resolve/{revision}/{quote(name, safe='/')}"
                        deadline = time.monotonic() + file_timeout
                        with opener(Request(url, headers=headers), timeout=timeout) as response:
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
            verify_snapshot(paths, cancelled=cancelled)
        except DownloadCancelled:
            event(StageStatus.CANCELLED)
            raise
        except BaseException:
            event(StageStatus.FAILED)
            raise
        event(StageStatus.COMPLETED)
    return snapshot_path(paths)


def main(argv=None):
    """Acquisition only; consent is recorded by the setup owner, not an env flag."""
    import argparse
    from h3server.events import encode_event
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    try:
        download_model(AppPaths(args.root), emit=lambda event: print(encode_event(event), flush=True))
    except Exception:
        raise SystemExit("model download failed; consent, storage, network or integrity check did not pass") from None


if __name__ == "__main__":
    main()
