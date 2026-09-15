"""URL canonicalization and helpers."""
from __future__ import annotations

import base64
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "ref", "ref_src", "cmpid", "oc",
    # listing/search state carried into detail links by Korean gov boards
    "curpage", "pageindex", "srchctgry", "srchkey", "srchtext", "srchbegindt", "srchenddt", "page",
}


def canonicalize(url: str) -> str:
    """Normalize URL for dedupe: lowercase host, strip tracking params, fragment, trailing slash, default ports."""
    url = url.strip()
    p = urlparse(url)
    scheme = (p.scheme or "https").lower()
    if scheme == "http":
        scheme = "https"  # treat http/https as the same resource
    host = (p.hostname or "").lower()
    if host.startswith("www.") and host.count(".") >= 2:
        host = host[4:]
    port = p.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", p.path or "/")
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    params = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=False) if k.lower() not in TRACKING_PARAMS]
    params.sort()
    query = urlencode(params, doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def is_google_news_redirect(url: str) -> bool:
    return "news.google.com" in host_of(url) and "/articles/" in url


def decode_google_news_url(url: str) -> str | None:
    """Best-effort decode of the legacy base64 form of Google News article URLs. Returns None if not decodable.
    Newer encodings need an HTTP resolution step (done in the adapter)."""
    m = re.search(r"/articles/([^/?#]+)", url)
    if not m:
        return None
    token = m.group(1)
    try:
        pad = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(token + pad)
    except Exception:
        return None
    # Legacy format: 0x08 0x13 0x22 <len> <url> ... ; find the first http(s) substring
    m2 = re.search(rb"https?://[^\x00-\x1f\x7f-\xff]+", raw)
    if not m2:
        return None
    candidate = m2.group(0).decode("utf-8", errors="ignore")
    candidate = re.split(r"[\x00-\x1f]", candidate)[0]
    return candidate if len(candidate) > 12 else None
