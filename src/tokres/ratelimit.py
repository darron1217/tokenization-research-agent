"""Per-host rate limiter and a shared HTTP client factory."""
from __future__ import annotations

import threading
import time
from collections import defaultdict

import httpx

from .settings import get_settings
from .urls import host_of

BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
DEFAULT_MIN_INTERVAL = 1.0  # seconds between requests to the same host
HOST_MIN_INTERVAL: dict[str, float] = {
    "sec.gov": 0.15,
    "efts.sec.gov": 0.15,
    "news.google.com": 2.0,
    "openapi.naver.com": 0.2,
    "api.hkma.gov.hk": 0.5,
    "api.gdeltproject.org": 6.0,
}


class RateLimiter:
    def __init__(self) -> None:
        self._last: dict[str, float] = defaultdict(float)
        self._lock = threading.Lock()

    def wait(self, url: str) -> None:
        host = host_of(url)
        interval = DEFAULT_MIN_INTERVAL
        for suffix, iv in HOST_MIN_INTERVAL.items():
            if host == suffix or host.endswith("." + suffix):
                interval = iv
                break
        with self._lock:
            now = time.monotonic()
            delta = now - self._last[host]
            if delta < interval:
                time.sleep(interval - delta)
            self._last[host] = time.monotonic()


_limiter = RateLimiter()


class PoliteClient(httpx.Client):
    """httpx client that respects per-host pacing and sets a UA."""

    def __init__(self, **kw):
        s = get_settings()
        headers = {"User-Agent": s.http_user_agent, "Accept-Language": "ko,en;q=0.8"}
        headers.update(kw.pop("headers", {}) or {})
        kw.setdefault("timeout", httpx.Timeout(30.0, connect=10.0))
        kw.setdefault("follow_redirects", True)
        super().__init__(headers=headers, **kw)

    def request(self, method, url, *a, **kw):  # type: ignore[override]
        _limiter.wait(str(url))
        r = super().request(method, url, *a, **kw)
        if r.status_code == 403 and method.upper() == "GET" and not kw.get("_retried"):
            # Some IR/newsroom hosts (Q4 etc.) reject non-browser UAs; retry once with a browser UA.
            _limiter.wait(str(url))
            headers = dict(kw.pop("headers", {}) or {})
            headers["User-Agent"] = BROWSER_UA
            headers.setdefault("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
            r = super().request(method, url, *a, headers=headers, **kw)
        return r
