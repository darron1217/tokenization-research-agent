"""Generic JSON list API (e.g. HKMA press releases).

extra: records_path "result.records", fields {title:..., link:..., date:...}, date_format, url with {offset},
       backfill {offset_step, max_pages}
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from ..models import RawItem, SourceSpec
from ..ratelimit import PoliteClient
from ._dates import parse_date


def _dig(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if obj is None:
            return None
        obj = obj.get(part) if isinstance(obj, dict) else None
    return obj


def parse_records(data: Any, spec: SourceSpec) -> list[RawItem]:
    x = spec.extra
    recs = _dig(data, x.get("records_path", "")) if x.get("records_path") else data
    fields = x.get("fields", {"title": "title", "link": "link", "date": "date"})
    out: list[RawItem] = []
    for rec in recs or []:
        title = str(rec.get(fields["title"], "")).strip()
        link = str(rec.get(fields["link"], "")).strip()
        if not title or not link:
            continue
        out.append(RawItem(url=link, title=title, source=spec.name,
                           published_at=parse_date(rec.get(fields.get("date", "date")), x.get("date_format"))))
    return out


class JsonApiAdapter:
    def _get(self, c: PoliteClient, spec: SourceSpec, offset: int) -> Any:
        assert spec.url
        r = c.get(spec.url.replace("{offset}", str(offset)))
        r.raise_for_status()
        return r.json()

    def discover(self, spec: SourceSpec, since: datetime) -> list[RawItem]:
        with PoliteClient() as c:
            items = parse_records(self._get(c, spec, 0), spec)
        return [i for i in items if not (i.published_at and i.published_at < since)]

    def backfill(self, spec: SourceSpec, date_from: datetime, date_to: datetime, cursor: str | None) -> Iterable[tuple[list[RawItem], str | None]]:
        bf = spec.extra.get("backfill") or {}
        step = int(bf.get("offset_step", 50))
        max_pages = int(bf.get("max_pages", 100))
        offset = int(cursor or 0)
        with PoliteClient() as c:
            for _ in range(max_pages):
                items = parse_records(self._get(c, spec, offset), spec)
                keep = [i for i in items if not i.published_at or date_from <= i.published_at <= date_to]
                dated = [i.published_at for i in items if i.published_at]
                if not items or (dated and max(dated) < date_from):
                    yield keep, None
                    return
                offset += step
                yield keep, str(offset)
            yield [], None
