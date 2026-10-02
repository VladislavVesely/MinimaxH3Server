"""One-use bootstrap plus non-expiring, explicitly revocable trusted-browser access."""

import hmac
import math
import secrets
import threading
import time


class BrowserSession:
    """Persistent credentials never expire on use or controller shutdown.

    A supplied finite lifetime supports isolated embedding/tests. Tokens and
    cookies are intentionally omitted from repr and public configuration.
    """

    def __init__(self, *, clock=time.monotonic, bootstrap_ttl: float = 120,
                 session_ttl: float | None = None, store=None, access_key=None):
        for value in (bootstrap_ttl, *(() if session_ttl is None else (session_ttl,))):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError('session lifetimes must be positive finite seconds')
        if store is not None and session_ttl is not None:
            raise ValueError('persistent access cannot have a session deadline')
        self._clock = clock
        self._lock = threading.RLock()
        self._store = store
        self.bootstrap_token = secrets.token_urlsafe(32)
        self._bootstrap_expires = clock() + bootstrap_ttl
        self._consumed = False
        self._authorized = False
        self._closed = False
        self._cookie = secrets.token_urlsafe(32)
        self._csrf = secrets.token_urlsafe(32)
        self._remember = secrets.token_urlsafe(32)
        self._access_key = None
        self.session_ttl = session_ttl
        self._session_expires = 0.0
        if store is not None:
            self._apply(store.initialize(key=access_key))

    def _apply(self, record):
        if self._cookie != record['cookie']:
            self._authorized = False
        self._cookie, self._csrf = record['cookie'], record['csrf']
        self._remember, self._access_key = record['remember'], record['key']

    def _refresh(self):
        if self._store is not None:
            self._apply(self._store.initialize())

    @staticmethod
    def _equal(candidate: str, expected: str) -> bool:
        return isinstance(candidate, str) and hmac.compare_digest(candidate.encode('utf-8', errors='surrogatepass'), expected.encode('utf-8'))

    def _authorize(self):
        self._authorized = True
        if self.session_ttl is not None:
            self._session_expires = self._clock() + self.session_ttl
        return self._cookie, self._csrf

    def consume(self, token: str) -> tuple[str, str] | None:
        with self._lock:
            self._refresh()
            if not self._equal(token, self.bootstrap_token) or self._consumed or self._clock() >= self._bootstrap_expires:
                return None
            self._consumed = True
            self.bootstrap_token = ''
            return self._authorize()

    def authorize_lan(self) -> tuple[str, str]:
        """Called only after the controller has verified its stable access key."""
        with self._lock:
            self._refresh()
            self._consumed = True
            self.bootstrap_token = ''
            return self._authorize()

    def restore(self, token):
        with self._lock:
            self._refresh()
            if self._closed or self._store is None or not self._equal(token, self._remember):
                return None
            return self._authorize()

    def authenticated(self, cookie: str) -> bool:
        with self._lock:
            self._refresh()
            matches = self._equal(cookie, self._cookie)
            if self._store is not None and matches:
                self._authorized = True
            return matches and self._authorized and self._alive()

    def _alive(self):
        return not self._closed and (self.session_ttl is None or self._clock() < self._session_expires)

    def csrf_valid(self, token: str) -> bool:
        with self._lock:
            self._refresh()
            return self._equal(token, self._csrf)

    @property
    def active(self) -> bool:
        with self._lock:
            self._refresh()
            return self._authorized and self._alive()

    @property
    def csrf_token(self) -> str:
        with self._lock:
            self._refresh()
            return self._csrf

    @property
    def remember_token(self):
        with self._lock:
            self._refresh()
            return self._remember if self._store is not None else None

    @property
    def access_key(self):
        with self._lock:
            self._refresh()
            return self._access_key

    @property
    def cookie_max_age(self):
        # Browsers cap persistent cookies. The opaque remembered grant restores
        # them without another key prompt even after the browser's own cap.
        return int(self.session_ttl) if self.session_ttl is not None else 400 * 24 * 60 * 60

    @property
    def cookie_header(self):
        with self._lock:
            self._refresh()
            return f'h3_session={self._cookie}; HttpOnly; Max-Age={self.cookie_max_age}; Path=/; SameSite=strict'

    def close(self):
        """Close this process's live credentials, without revoking browser trust."""
        with self._lock:
            self._authorized = False
            self._closed = True
            self._consumed = True
            self.bootstrap_token = ''

    def invalidate(self):
        """Explicit access reset: revoke cookies, browser grants and the old key."""
        with self._lock:
            was_closed = self._closed
            self.close()
            self._closed = was_closed
            if self._store is not None:
                self._apply(self._store.reset())
            else:
                self._cookie = secrets.token_urlsafe(32)
                self._csrf = secrets.token_urlsafe(32)
                self._remember = secrets.token_urlsafe(32)
            self._session_expires = 0.0
