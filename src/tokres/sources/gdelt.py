"""GDELT 2.0 DOC API adapter (free, no key, direct publisher URLs, Korean via sourcelang:korean).
Strictly rate limited (≥5s between calls) — keep query lists short."""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from ..config import topic_queries
from ..models import RawItem, SourceSpec
from ..ratelimit import PoliteClient

log = logging.getLogger(__name__)
ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"


def parse_articles(data: dict, source: str, query: str) -> list[RawItem]:
    out = []
    for a in data.get("articles", []):
        url, title = a.get("url"), (a.get("title") or "").strip()
        if not url or not title:
            continue
        try:
            seen = datetime.strptime(a.get("seendate", ""), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except ValueError:
            seen = None
        out.append(RawItem(url=url, title=title, source=source, published_at=seen,
                           extra={"query": query, "domain": a.get("domain"), "language": a.get("language")}))
    return out


class GdeltAdapter:
    def _query(self, c: PoliteClient, spec: SourceSpec, q: str, **params) -> list[RawItem]:
        lang = spec.extra.get("sourcelang")
        full = f"({q}) sourcelang:{lang}" if lang else q
        r = c.get(ENDPOINT, params={"query": full, "mode": "artlist", "format": "json", "maxrecords": 75, "sort": "DateDesc", **params})
        if r.status_code == 429:
            raise RuntimeError("GDELT rate limited (429)")
        r.raise_for_status()
        if "json" not in (r.headers.get("content-type") or ""):
            return []
        return parse_articles(r.json(), spec.name, q)

    def discover(self, spec: SourceSpec, since: datetime) -> list[RawItem]:
        days = max(1, min(30, (datetime.now(UTC) - since).days + 1))
        seen: dict[str, RawItem] = {}
        with PoliteClient() as c:
            for q in topic_queries(spec.extra.get("queries_from", "topics.gdelt_queries_en")):
                try:
                    for it in self._query(c, spec, q, timespan=f"{days}d"):
                        seen.setdefault(it.url, it)
                except Exception as e:  # noqa: BLE001
                    log.warning("gdelt query %r failed: %s", q, e)
                    if "429" in str(e):
                        break
        return list(seen.values())

    def backfill(self, spec: SourceSpec, date_from: datetime, date_to: datetime, cursor: str | None) -> Iterable[tuple[list[RawItem], str | None]]:
        win_end = datetime.fromisoformat(cursor) if cursor else date_to
        win_start = max(date_from, win_end - timedelta(days=14))
        seen: dict[str, RawItem] = {}
        with PoliteClient() as c:
            for q in topic_queries(spec.extra.get("queries_from", "topics.gdelt_queries_en")):
                try:
                    for it in self._query(c, spec, q, startdatetime=win_start.strftime("%Y%m%d%H%M%S"),
                                          enddatetime=win_end.strftime("%Y%m%d%H%M%S")):
                        seen.setdefault(it.url, it)
                except Exception as e:  # noqa: BLE001
                    log.warning("gdelt backfill query %r failed: %s", q, e)
        yield list(seen.values()), (None if win_start <= date_from else win_start.isoformat())
