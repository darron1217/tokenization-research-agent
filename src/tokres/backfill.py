"""Backfill: walk paginated sources over a date range; resumable via backfill_progress."""
from __future__ import annotations

import logging
from datetime import datetime

from .db import Database, utcnow
from .models import RunStats, SourceSpec
from .sources.base import get_adapter, persist_items

log = logging.getLogger(__name__)


def backfill_source(db: Database, spec: SourceSpec, date_from: datetime, date_to: datetime, *, resume: bool = True,
                    dry_run: bool = False, max_batches: int | None = None) -> RunStats:
    stats = RunStats(stage=f"backfill:{spec.name}")
    bf = spec.extra.get("backfill") or {}
    if spec.type == "rss":
        if not bf.get("url"):
            stats.note(f"{spec.name}: RSS source without backfill.url (archive listing); skipped")
            return stats
        # RSS source with an archive listing spec → drive the html_list adapter with the backfill sub-spec
        spec = spec.model_copy(update={"type": bf.get("type", "html_list"), "url": bf["url"]})
        spec.extra = {**{k: v for k, v in spec.extra.items() if k != "backfill"}, **{k: v for k, v in bf.items() if k != "url"},
                      "backfill": bf}
    adapter = get_adapter(spec.type)
    if not hasattr(adapter, "backfill"):
        stats.note(f"{spec.name}: adapter {spec.type} has no backfill support")
        return stats
    key = (spec.name, date_from.date().isoformat(), date_to.date().isoformat())
    row = db.q1("SELECT cursor, status, fetched FROM backfill_progress WHERE source=? AND window_from=? AND window_to=?", key)
    if row and row["status"] == "done" and resume:
        stats.note(f"{spec.name}: window already done ({row['fetched']} fetched)")
        return stats
    cursor = row["cursor"] if (row and resume) else None
    fetched = int(row["fetched"]) if (row and resume) else 0
    if not dry_run:
        with db.tx():
            db.x("""INSERT OR REPLACE INTO backfill_progress(source,window_from,window_to,cursor,status,fetched)
                    VALUES (?,?,?,?,?,?)""", (*key, cursor, "running", fetched))
    batches = 0
    try:
        for items, next_cursor in adapter.backfill(spec, date_from, date_to, cursor):
            batches += 1
            if dry_run:
                stats.ok += len(items)
                for it in items[:5]:
                    stats.note(f"  {it.published_at.date() if it.published_at else '-'} {it.title[:70]} {it.url}")
                stats.note(f"batch {batches}: {len(items)} items, next={next_cursor}")
            else:
                with db.tx():
                    n = persist_items(db, spec, items, stats, gate_news=True)
                    fetched += n
                    db.x("UPDATE backfill_progress SET cursor=?, fetched=? WHERE source=? AND window_from=? AND window_to=?",
                         (next_cursor, fetched, *key))
                log.info("backfill %s batch %d: %d seen, %d new, next=%s", spec.name, batches, len(items), n, next_cursor)
            if next_cursor is None:
                break
            if max_batches and batches >= max_batches:
                stats.note(f"stopped after {batches} batches (cursor={next_cursor})")
                return stats
        if not dry_run:
            with db.tx():
                db.x("UPDATE backfill_progress SET status='done', done_at=? WHERE source=? AND window_from=? AND window_to=?",
                     (utcnow(), *key))
    except Exception as e:  # noqa: BLE001
        stats.failed += 1
        stats.note(f"{spec.name}: FAILED {e!r}")
        log.warning("backfill %s failed: %r", spec.name, e)
    return stats
