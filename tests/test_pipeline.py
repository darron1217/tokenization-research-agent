"""End-to-end without network/LLM: persist fixtures → stub triage/enrich → score → brief → index (in-memory Qdrant)."""
from __future__ import annotations

import hashlib
from datetime import UTC

from qdrant_client import QdrantClient

from tests.conftest import FIX, spec
from tokres.brief.render import build_brief
from tokres.index.embed import HashEmbedder
from tokres.index.pipeline import index_pending
from tokres.index.qdrant_store import QdrantStore
from tokres.llm.stages import run_enrich, run_triage
from tokres.llm.stub_analyzer import StubAnalyzer
from tokres.models import RunStats, Status
from tokres.scoring.rules import compute_scores, score_item
from tokres.sources.base import persist_items
from tokres.sources.rss import parse_feed


def _seed(db, since):
    items = parse_feed((FIX / "fsc.rss").read_bytes(), "fsc_press", since)
    stats = RunStats(stage="discover")
    with db.tx():
        n = persist_items(db, spec("fsc_press"), items, stats)
    return n


def test_persist_dedupes_and_gates(db, since):
    assert _seed(db, since) == 2
    assert _seed(db, since) == 0  # idempotent
    # news source with zero keywords is gated out
    from tokres.models import RawItem

    stats = RunStats(stage="x")
    with db.tx():
        n = persist_items(db, spec("google_news_kr"), [RawItem(url="https://example.com/w", title="오늘의 날씨", source="g")], stats)
    assert n == 0


def test_full_pipeline_no_llm(db, since, tmp_path, monkeypatch):
    _seed(db, since)
    # fake fetch: set content directly
    with db.tx():
        for it in db.items_by_status(Status.discovered):
            text = f"{it.title}. 토큰증권 제도 관련 상세 본문입니다. " * 20
            db.update_item(it.id, content_text=text, extract_kind="html", lang="ko",
                           content_hash=hashlib.sha1(text.encode()).hexdigest())
            db.set_status(it.id, Status.fetched)
    an = StubAnalyzer()
    assert run_triage(db, an).ok == 2
    assert run_enrich(db, an).ok >= 1
    compute_scores(db)
    pri = {r["item_id"]: r["priority"] for r in db.q("SELECT item_id, priority FROM scores")}
    assert pri and all(p in ("must_read", "notable", "fyi", "archive") for p in pri.values())
    monkeypatch.setattr("tokres.settings.Settings.reports_dir", property(lambda self: tmp_path))
    out = build_brief(db, an, "2026-09-15", send_slack=False)
    assert out and out.exists()
    md = out.read_text()
    assert "토큰증권" in md and "오늘의 핵심" in md
    assert db.q1("SELECT COUNT(*) c FROM items WHERE brief_date='2026-09-15'")["c"] == 2
    # index into in-memory qdrant with hash embedder (no sparse to avoid model download)
    store = QdrantStore(QdrantClient(":memory:"), HashEmbedder(), sparse=False)
    st = index_pending(db, None, store=store)
    assert st.ok >= 1
    pts = store.search_dense(store.embedder.embed_query("토큰증권 자본시장법"), k=5)
    assert pts and pts[0].payload["item_id"] in pri
    # re-index is an upsert (same point ids)
    before = store.client.count(store.collection).count
    with db.tx():
        db.x("UPDATE items SET status='enriched' WHERE status='indexed'")
    index_pending(db, None, store=store)
    assert store.client.count(store.collection).count == before


def test_scoring_rules():
    from datetime import datetime

    now = datetime(2026, 9, 15, tzinfo=UTC)
    r = score_item(tier="T0", entity="금융위원회", doc_type="regulation", keyword_score=9, corroboration=2,
                   published_at=now, materiality=5, novelty="new", impact_type="regulatory_change", relevance="core", now=now)
    assert r.priority == "must_read"
    r2 = score_item(tier="T4", entity="JPMorgan", doc_type="press_release", keyword_score=10, corroboration=0, published_at=now,
                    materiality=5, novelty="new", impact_type="infrastructure_launch", relevance="core", now=now)
    assert r2.final >= 35 and r2.priority == "fyi"  # would be notable, but unregistered social is capped
    r2b = score_item(tier="T4", entity="JPMorgan", doc_type="press_release", keyword_score=10, corroboration=1, published_at=now,
                     materiality=5, novelty="new", impact_type="infrastructure_launch", relevance="core", now=now)
    assert r2b.priority == "notable"  # corroborated by another source → cap lifted
    r3 = score_item(tier="T0", entity="BIS", doc_type="report", keyword_score=5, corroboration=0, published_at=now,
                    materiality=4, novelty="repeat", impact_type="research", relevance="core", now=now)
    assert r3.priority == "archive"


def test_prompt_stability():
    """System prompts must be byte-identical across instances (prompt caching)."""
    from tokres.llm.analyzer import load_prompt

    assert load_prompt("triage") == load_prompt("triage")
    from tokres.llm.anthropic_analyzer import _institution_block

    assert _institution_block() == _institution_block()
