"""Downloader-internal POSIX transport, not a URL API or inference worker.

Only this short-lived child may block in DNS/TLS/urllib/header/body IO. The parent
polls cancellation and one absolute monotonic deadline from before process startup,
then terminates, kills if necessary, and reaps the child. A control-pipe watchdog
also exits the child on parent death or deadline, even if stdout is backpressured.
IPC is one bounded JSON header line followed by raw bytes (no pickle, queues or
unbounded reader threads). Only the parent writes or promotes model files.
Imports are inert on controller platforms; execution is on the POSIX model host.
"""
import json
import math
import os
import selectors
import subprocess
import sys
import threading
import time
from urllib.parse import quote
from urllib.request import Request, build_opener

CHUNK = 1024 * 1024
HEADER_LIMIT = 16384
REQUEST_LIMIT = 4096
POLL_SECONDS = .05
RESPONSE_HEADERS = ("Content-Length", "Content-Range", "Content-Encoding")


class DownloadTransport:
    """Private response-compatible context; command is a Python test injection seam."""
    def __init__(self, paths, name, offset, *, timeout, deadline, cancelled, command=None):
        from h3server.backends import load_manifest
        self.paths, self.deadline, self.cancelled = paths, deadline, cancelled
        self.document = {"name": name, "offset": offset, "timeout": timeout, "deadline": deadline,
                         "revision": load_manifest()["model"]["revision"]}
        self.command = command or (sys.executable, "-m", "h3server._download_transport")
        self.process = self.selector = None
        self.pending = b""

    def _check(self):
        from h3server.downloads import DownloadCancelled
        if self.cancelled():
            raise DownloadCancelled("download cancelled")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("artifact transfer deadline")
        return min(POLL_SECONDS, remaining)

    def __enter__(self):
        try:
            self._check()
            payload = json.dumps(self.document, allow_nan=False).encode("ascii") + b"\n"
            if len(payload) > REQUEST_LIMIT:
                raise ValueError("transport request limit")
            env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR") if key in os.environ}
            env.update(self.paths.cache_environment())
            env.update(HOME=str(self.paths.root), PYTHONNOUSERSITE="1")
            self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, env=env, bufsize=0)
            self._check()
            # One <=PIPE_BUF request into a new empty pipe; leave it open as the
            # child's parent-liveness signal. Never send a URL or arbitrary headers.
            self.process.stdin.write(payload)
            os.set_blocking(self.process.stdout.fileno(), False)
            self.selector = selectors.DefaultSelector()
            self.selector.register(self.process.stdout, selectors.EVENT_READ)
            header = b""
            while b"\n" not in header:
                chunk = self._read_raw(HEADER_LIMIT + 1 - len(header))
                if not chunk:
                    raise ValueError("transport exited before response headers")
                header += chunk
                if len(header.split(b"\n", 1)[0]) > HEADER_LIMIT:
                    raise ValueError("transport header limit")
            line, self.pending = header.split(b"\n", 1)
            metadata = json.loads(line)
            if (type(metadata) is not dict or set(metadata) != {"status", "headers"}
                or type(metadata["status"]) is not int or type(metadata["headers"]) is not dict
                or set(metadata["headers"]) - set(RESPONSE_HEADERS)
                or any(type(value) is not str for value in metadata["headers"].values())):
                raise ValueError("invalid transport response headers")
            self.status, self.headers = metadata["status"], metadata["headers"]
            return self
        except BaseException:
            self._close()
            raise

    def _read_raw(self, limit):
        while True:
            wait = self._check()
            if not self.selector.select(wait):
                continue
            try:
                chunk = os.read(self.process.stdout.fileno(), limit)
            except BlockingIOError:
                continue
            self._check()
            if not chunk:
                # EOF alone is not success: a child can crash after sending data,
                # or close stdout and hang. Check its exit under the same deadline.
                while self.process.poll() is None:
                    time.sleep(self._check())
                if self.process.returncode:
                    raise ValueError("model transport failed")
            return chunk

    def read(self, size):
        self._check()
        if not 0 < size <= CHUNK:
            raise ValueError("transport read limit")
        if self.pending:
            chunk, self.pending = self.pending[:size], self.pending[size:]
            return chunk
        return self._read_raw(size)

    def _close(self):
        if self.selector is not None:
            self.selector.close()
        if self.process is not None:
            try:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=.2)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait()
            finally:
                self.process.stdin.close()
                self.process.stdout.close()

    def __exit__(self, *exc):
        self._close()


def _watch_parent_and_deadline(deadline):
    """No file/network writes in this watchdog; death/deadline interrupts urllib."""
    with selectors.DefaultSelector() as selector:
        selector.register(sys.stdin, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or selector.select(min(POLL_SECONDS, remaining)):
                # EOF means parent died/closed; extra input is a protocol violation.
                os._exit(1)


def _fetch(document, *, opener=None):
    """Child implementation; opener is injectable only via Python boundary tests."""
    from h3server.backends import load_manifest
    from h3server.downloads import _Redirects, _inventory, require_consent
    from h3server.paths import AppPaths
    manifest = load_manifest()
    entries = {entry["rfilename"]: entry for entry in _inventory(manifest)}
    if (type(document) is not dict or set(document) != {"name", "offset", "timeout", "deadline", "revision"}
        or type(document["name"]) is not str or document["name"] not in entries
        or document["revision"] != manifest["model"]["revision"]
        or type(document["offset"]) is not int or not 0 <= document["offset"] < entries[document["name"]]["size"]
        or any(type(document[k]) not in (int, float) or not math.isfinite(document[k]) for k in ("timeout", "deadline"))
        or not 0 < document["timeout"] <= 30 or not 0 < document["deadline"] - time.monotonic() <= 6 * 3600):
        raise ValueError("invalid pinned transport request")
    # HOME is the parent's explicit managed root, not the account's global home.
    # Also guard direct invocation/revocation between parent approval and startup.
    require_consent(AppPaths(os.environ["HOME"]))
    threading.Thread(target=_watch_parent_and_deadline, args=(document["deadline"],), daemon=True).start()
    headers = {"Accept-Encoding": "identity", "User-Agent": "h3server-pinned-downloader/1"}
    if document["offset"]:
        headers["Range"] = f"bytes={document['offset']}-"
    url = f"https://huggingface.co/MiniMaxAI/MiniMax-H3/resolve/{document['revision']}/{quote(document['name'], safe='/')}"
    opener = opener or build_opener(_Redirects()).open
    with opener(Request(url, headers=headers), timeout=document["timeout"]) as response:
        # urllib itself bounds line/count parsing; tighten the whole header block
        # before IPC and reject ambiguous integrity-related duplicate headers.
        if sum(len(k) + len(v) + 4 for k, v in response.headers.items()) > HEADER_LIMIT:
            raise ValueError("HTTP response header limit")
        selected = {}
        for name in RESPONSE_HEADERS:
            values = response.headers.get_all(name, [])
            if len(values) > 1:
                raise ValueError("duplicate model response header")
            if values:
                selected[name] = values[0]
        payload = json.dumps({"status": response.status, "headers": selected}).encode("ascii")
        if len(payload) > HEADER_LIMIT:
            raise ValueError("transport header limit")
        sys.stdout.buffer.write(payload + b"\n")
        sys.stdout.buffer.flush()
        # read1 streams available data instead of accumulating a MiB. It is NOT
        # the cancellation guarantee: parent supervision also bounds open/read1.
        remaining = entries[document["name"]]["size"] + 1
        while remaining:
            chunk = response.read1(min(CHUNK, remaining))
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            remaining -= len(chunk)


def main():
    # This internal entry point accepts a pinned name/revision/offset over stdin,
    # never a caller URL, shell command, filesystem destination or model payload.
    line = sys.stdin.buffer.readline(REQUEST_LIMIT + 1)
    if len(line) > REQUEST_LIMIT or not line.endswith(b"\n"):
        raise SystemExit(1)
    try:
        _fetch(json.loads(line))
    except Exception:
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
