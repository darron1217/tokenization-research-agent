from __future__ import annotations

from datetime import datetime

import feedparser

from ..models import RawItem, SourceSpec
from ..ratelimit import PoliteClient
from ._dates import parse_date


def parse_feed(content: bytes | str, source: str, since: datetime | None = None) -> list[RawItem]:
    feed = feedparser.parse(content)
    out: list[RawItem] = []
    for e in feed.entries:
        link = e.get("link") or ""
        title = (e.get("title") or "").strip()
        if not link or not title:
            continue
        pub = parse_date(e.get("published_parsed") or e.get("updated_parsed") or e.get("published") or e.get("updated"))
        if since and pub and pub < since:
            continue
        summary = e.get("summary") or e.get("description") or ""
        out.append(RawItem(url=link, title=title, source=source, published_at=pub, snippet=_strip_html(summary)[:2000]))
    return out


def _strip_html(s: str) -> str:
    from selectolax.parser import HTMLParser

    if "<" not in s:
        return s.strip()
    return HTMLParser(s).text(separator=" ").strip()


class RssAdapter:
    def discover(self, spec: SourceSpec, since: datetime) -> list[RawItem]:
        assert spec.url
        with PoliteClient() as c:
            r = c.get(spec.url)
            r.raise_for_status()
            return parse_feed(r.content, spec.name, since)
