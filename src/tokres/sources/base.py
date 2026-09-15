from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from ..config import domain_info, keyword_score
from ..db import Database
from ..models import RawItem, RunStats, SourceSpec
from ..urls import canonicalize, host_of

log = logging.getLogger(__name__)


class SourceAdapter(Protocol):
    def discover(self, spec: SourceSpec, since: datetime) -> list[RawItem]: ...


class BackfillAdapter(Protocol):
    def backfill(self, spec: SourceSpec, date_from: datetime, date_to: datetime, cursor: str | None) -> Iterable[tuple[list[RawItem], str | None]]:
        """Yield (items, next_cursor). next_cursor None means done."""
        ...


def get_adapter(kind: str):
    from . import bird_x, gdelt, google_news, html_list, json_api, naver, rss

    return {
        "rss": rss.RssAdapter(),
        "html_list": html_list.HtmlListAdapter(),
        "json_api": json_api.JsonApiAdapter(),
        "naver_news": naver.NaverNewsAdapter(),
        "google_news": google_news.GoogleNewsAdapter(),
        "gdelt": gdelt.GdeltAdapter(),
        "bird_x": bird_x.BirdXAdapter(),
    }[kind]


def resolve_tier(raw: RawItem, spec: SourceSpec) -> tuple[str, str | None, str | None]:
    """Decide (tier, entity, region) for an item. Explicit raw overrides > domain table (for discovery sources) > spec."""
    if raw.tier:
        return raw.tier, raw.entity or spec.entity, raw.region or spec.region
    if spec.type in ("naver_news", "google_news", "gdelt", "bird_x"):
        info = domain_info(host_of(raw.url))
        if info:
            return info["tier"], info.get("entity"), info.get("region", spec.region)
        return "T3", None, spec.region
    return spec.tier, spec.entity, spec.region


def persist_items(db: Database, spec: SourceSpec, items: list[RawItem], stats: RunStats, *, gate_news: bool = True) -> int:
    """Insert RawItems; apply the free keyword gate for non-primary sources. Returns number inserted."""
    inserted = 0
    for raw in items:
        if not raw.url or not raw.title:
            continue
        tier, entity, region = resolve_tier(raw, spec)
        score, _hits = keyword_score(f"{raw.title}\n{raw.snippet or ''}")
        if gate_news and tier in ("T2", "T3", "T4") and score == 0:
            continue  # zero keyword hits on a non-primary source → not worth fetching
        canon = canonicalize(raw.url)
        new_id = db.insert_item(
            canonical_url=canon, url=raw.url, source=spec.name, source_tier=tier, entity=entity, region=region,
            title=raw.title.strip()[:500], snippet=(raw.snippet or "")[:2000] or None,
            published_at=raw.published_at, keyword_score=score, extra=raw.extra,
        )
        if new_id:
            inserted += 1
    stats.ok += inserted
    return inserted


def discover_all(db: Database, specs: list[SourceSpec], since: datetime | None = None, *, tiers: set[str] | None = None,
                 only: set[str] | None = None) -> RunStats:
    since = since or datetime.now(UTC) - timedelta(days=3)
    stats = RunStats(stage="discover")
    run_id = db.start_run("discover")
    for spec in specs:
        if not spec.enabled:
            continue
        if only and spec.name not in only:
            continue
        if tiers and spec.tier not in tiers:
            continue
        try:
            adapter = get_adapter(spec.type)
            items = adapter.discover(spec, since)
            with db.tx():
                n = persist_items(db, spec, items, stats)
                db.record_source_health(spec.name, True)
            stats.note(f"{spec.name}: {len(items)} seen, {n} new")
            log.info("discover %s: %d seen, %d new", spec.name, len(items), n)
        except Exception as e:  # one adapter failing must not stop the rest
            stats.failed += 1
            with db.tx():
                fails = db.record_source_health(spec.name, False, repr(e))
            stats.note(f"{spec.name}: FAILED ({fails}x) {e!r}"[:300])
            log.warning("discover %s failed: %r", spec.name, e)
    with db.tx():
        db.finish_run(run_id, stats)
    return stats
