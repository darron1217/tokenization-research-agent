"""Naver News Search API adapter (discovery source, snippet-only; full text fetched later from originallink)."""
from __future__ import annotations

import html
import re
from collections.abc import Iterable
from datetime import datetime, timedelta

from ..config import topic_queries
from ..models import RawItem, SourceSpec
from ..ratelimit import PoliteClient
from ..settings import get_settings
from ._dates import parse_date

ENDPOINT = "https://openapi.naver.com/v1/search/news.json"
_TAG = re.compile(r"<[^>]+>")


def _clean(s: str) -> str:
    return html.unescape(_TAG.sub("", s or "")).strip()


def parse_response(data: dict, source: str, query: str) -> list[RawItem]:
    out: list[RawItem] = []
    for it in data.get("items", []):
        url = it.get("originallink") or it.get("link")
        if not url:
            continue
        out.append(RawItem(url=url, title=_clean(it.get("title", "")), source=source,
                           published_at=parse_date(it.get("pubDate")), snippet=_clean(it.get("description", "")),
                           extra={"naver_link": it.get("link"), "query": query}))
    return out


class NaverNewsAdapter:
    def _headers(self) -> dict[str, str]:
        s = get_settings()
        if not s.has_naver:
            raise RuntimeError("NAVER_CLIENT_ID/SECRET not set; naver_news adapter disabled")
        return {"X-Naver-Client-Id": s.naver_client_id or "", "X-Naver-Client-Secret": s.naver_client_secret or ""}

    def discover(self, spec: SourceSpec, since: datetime) -> list[RawItem]:
        headers = self._headers()
        queries = topic_queries(spec.extra.get("queries_from", "topics.discovery_queries_ko"))
        seen: dict[str, RawItem] = {}
        with PoliteClient(headers=headers) as c:
            for q in queries:
                r = c.get(ENDPOINT, params={"query": q, "display": 50, "sort": "date"})
                if r.status_code == 429:
                    break
                r.raise_for_status()
                for it in parse_response(r.json(), spec.name, q):
                    if it.published_at and it.published_at < since:
                        continue
                    seen.setdefault(it.url, it)
        return list(seen.values())

    def backfill(self, spec: SourceSpec, date_from: datetime, date_to: datetime, cursor: str | None) -> Iterable[tuple[list[RawItem], str | None]]:
        """Walk month windows newest→oldest; per query pull up to 1000 results sorted by date, stop when older than window."""
        headers = self._headers()
        queries = topic_queries(spec.extra.get("queries_from", "topics.discovery_queries_ko"))
        # cursor: ISO date of the window end being processed
        win_end = datetime.fromisoformat(cursor) if cursor else date_to
        win_start = max(date_from, win_end - timedelta(days=30))
        seen: dict[str, RawItem] = {}
        with PoliteClient(headers=headers) as c:
            for q in queries:
                for start in range(1, 1001, 100):
                    r = c.get(ENDPOINT, params={"query": q, "display": 100, "start": start, "sort": "date"})
                    if r.status_code != 200:
                        break
                    items = parse_response(r.json(), spec.name, q)
                    if not items:
                        break
                    older = False
                    for it in items:
                        if it.published_at and it.published_at < win_start:
                            older = True
                            continue
                        if it.published_at and it.published_at > win_end:
                            continue
                        seen.setdefault(it.url, it)
                    if older:
                        break
        next_cursor = None if win_start <= date_from else win_start.isoformat()
        yield list(seen.values()), next_cursor
