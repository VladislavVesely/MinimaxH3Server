"""Memory-only, one-use browser bootstrap and constant-time session checks."""

import hmac
import math
import secrets
import threading
import time


class BrowserSession:
    """One local browser session. Launch with a URL fragment, never a query token.

    Tokens and cookies are intentionally omitted from object repr. Restarting
    the controller invalidates every session; credentials are not persisted.
    """

    def __init__(self, *, clock=time.monotonic, bootstrap_ttl: float = 120,
                 session_ttl: float = 8 * 60 * 60):
        for value in (bootstrap_ttl, session_ttl):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("session lifetimes must be positive finite seconds")
        self._clock = clock
        self._lock = threading.Lock()
        self.bootstrap_token = secrets.token_urlsafe(32)
        self._bootstrap_expires = clock() + bootstrap_ttl
        self._consumed = False
        self._cookie = secrets.token_urlsafe(32)
        self._csrf = secrets.token_urlsafe(32)
        self.session_ttl = session_ttl
        self._session_expires = 0.0

    @staticmethod
    def _equal(candidate: str, expected: str) -> bool:
        return isinstance(candidate, str) and hmac.compare_digest(candidate.encode("utf-8", errors="surrogatepass"), expected.encode("utf-8"))

    def consume(self, token: str) -> tuple[str, str] | None:
        with self._lock:
            matches = self._equal(token, self.bootstrap_token)
            if not matches or self._consumed or self._clock() >= self._bootstrap_expires:
                return None
            self._consumed = True
            self.bootstrap_token = ""  # drop the consumed launch token
            self._session_expires = self._clock() + self.session_ttl
            return self._cookie, self._csrf

    def authorize_lan(self) -> tuple[str, str]:
        """Called only after the controller has verified its LAN access key."""
        with self._lock:
            self._consumed = True
            self.bootstrap_token = ''
            self._session_expires = self._clock() + self.session_ttl
            return self._cookie, self._csrf

    def authenticated(self, cookie: str) -> bool:
        with self._lock:
            matches = self._equal(cookie, self._cookie)
            return matches and self._consumed and self._clock() < self._session_expires

    def csrf_valid(self, token: str) -> bool:
        return self._equal(token, self._csrf)

    @property
    def active(self) -> bool:
        with self._lock:
            return self._consumed and self._clock() < self._session_expires

    @property
    def csrf_token(self) -> str:
        return self._csrf

    def invalidate(self) -> None:
        with self._lock:
            self._consumed = True
            self._session_expires = 0.0
            self.bootstrap_token = ""
            self._cookie = secrets.token_urlsafe(32)
            self._csrf = secrets.token_urlsafe(32)
