"""run_daily: discover → fetch → triage → enrich → score → brief → index, with per-stage failure isolation."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from .config import load_sources
from .db import Database
from .extract import fetch_pending
from .llm.anthropic_analyzer import make_analyzer
from .llm.stages import run_enrich, run_triage
from .models import RunStats
from .notify.slack import post_warning
from .scoring.rules import compute_scores
from .settings import get_settings
from .sources.base import discover_all

log = logging.getLogger(__name__)


def source_warnings(db: Database, threshold: int = 3) -> list[str]:
    rows = db.q("SELECT source, consecutive_failures, last_error FROM source_health WHERE consecutive_failures >= ?", (threshold,))
    return [f"{r['source']} 연속 {r['consecutive_failures']}회 실패: {(r['last_error'] or '')[:80]}" for r in rows]


def run_daily(db: Database, *, limit: int | None = None, use_llm: bool = True, send_slack: bool = True,
              do_index: bool = True, since_days: int = 3, only_sources: set[str] | None = None) -> dict[str, RunStats]:
    s = get_settings()
    results: dict[str, RunStats] = {}
    analyzer = make_analyzer(db, use_llm)
    since = datetime.now(UTC) - timedelta(days=since_days)

    def stage(name, fn):
        try:
            results[name] = fn()
            log.info("%s: ok=%d failed=%d", name, results[name].ok, results[name].failed)
        except Exception as e:
            log.exception("stage %s crashed", name)
            results[name] = RunStats(stage=name, failed=1, notes=[repr(e)])

    stage("discover", lambda: discover_all(db, load_sources(), since, only=only_sources))
    stage("fetch", lambda: fetch_pending(db, limit))
    stage("triage", lambda: run_triage(db, analyzer, limit))
    stage("enrich", lambda: run_enrich(db, analyzer, limit, max_items=s.enrich_daily_max))
    stage("score", lambda: compute_scores(db))
    warnings = source_warnings(db)

    def _brief():
        from .brief.render import build_brief

        out = build_brief(db, analyzer, send_slack=send_slack, warnings=warnings)
        st = RunStats(stage="brief", ok=1 if out else 0)
        if out:
            st.note(str(out))
        return st

    stage("brief", _brief)
    if do_index:
        def _index():
            from .index.pipeline import index_pending

            return index_pending(db, analyzer if use_llm else None, limit)

        stage("index", _index)
    if warnings and send_slack and s.has_slack and not results.get("brief", RunStats(stage="brief")).ok:
        post_warning("; ".join(warnings))
    return results
