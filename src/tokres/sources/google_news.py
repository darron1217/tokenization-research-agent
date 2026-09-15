"""Google News RSS search adapter. Resolves redirect URLs to the publisher URL."""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta
from urllib.parse import quote

from ..config import topic_queries
from ..models import RawItem, SourceSpec
from ..ratelimit import PoliteClient
from ..urls import decode_google_news_url, is_google_news_redirect
from .rss import parse_feed

log = logging.getLogger(__name__)


def feed_url(query: str, hl: str, gl: str, ceid: str) -> str:
    return f"https://news.google.com/rss/search?q={quote(query)}&hl={hl}&gl={gl}&ceid={quote(ceid)}"


def resolve_url(c: PoliteClient, url: str) -> str:
    """Google News article links are redirects. Try offline decode first, then an HTTP HEAD/GET."""
    if not is_google_news_redirect(url):
        return url
    decoded = decode_google_news_url(url)
    if decoded:
        return decoded
    try:
        r = c.get(url, follow_redirects=True, timeout=15)
        final = str(r.url)
        if not is_google_news_redirect(final):
            return final
        # Newer format: publisher URL lives in a data attribute / meta refresh in the HTML
        from selectolax.parser import HTMLParser

        tree = HTMLParser(r.text)
        a = tree.css_first("a[href^='http']:not([href*='google'])")
        if a is not None:
            return a.attributes["href"]
    except Exception as e:  # noqa: BLE001
        log.debug("google news resolve failed for %s: %r", url, e)
    return url


class GoogleNewsAdapter:
    def _fetch_queries(self, spec: SourceSpec, queries: list[str], since: datetime | None,
                       resolve: bool = True) -> list[RawItem]:
        x = spec.extra
        seen: dict[str, RawItem] = {}
        with PoliteClient() as c:
            for q in queries:
                try:
                    r = c.get(feed_url(q, x.get("hl", "ko"), x.get("gl", "KR"), x.get("ceid", "KR:ko")))
                    r.raise_for_status()
                except Exception as e:  # noqa: BLE001
                    log.warning("google news query %r failed: %r", q, e)
                    continue
                for it in parse_feed(r.content, spec.name, since):
                    it.extra["query"] = q
                    it.extra["google_url"] = it.url
                    # Publisher name is in <source>; title often ends with " - Publisher"
                    if " - " in it.title:
                        it.title = it.title.rsplit(" - ", 1)[0].strip()
                    if resolve and spec.extra.get("resolve_urls", False):
                        it.url = resolve_url(c, it.url)
                    seen.setdefault(it.url, it)
        return list(seen.values())

    def discover(self, spec: SourceSpec, since: datetime) -> list[RawItem]:
        queries = topic_queries(spec.extra.get("queries_from", "topics.discovery_queries_ko"))
        return self._fetch_queries(spec, queries, since)

    def backfill(self, spec: SourceSpec, date_from: datetime, date_to: datetime, cursor: str | None) -> Iterable[tuple[list[RawItem], str | None]]:
        base = topic_queries(spec.extra.get("queries_from", "topics.discovery_queries_ko"))
        win_end = datetime.fromisoformat(cursor) if cursor else date_to
        win_start = max(date_from, win_end - timedelta(days=30))
        qs = [f"{q} after:{win_start:%Y-%m-%d} before:{win_end:%Y-%m-%d}" for q in base]
        items = self._fetch_queries(spec, qs, None)
        next_cursor = None if win_start <= date_from else win_start.isoformat()
        yield items, next_cursor
