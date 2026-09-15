"""Deterministic analyzer for --no-llm runs and tests."""
from __future__ import annotations

from ..config import find_institutions, keyword_score
from .analyzer import BriefItemForLLM, ItemForLLM
from .schemas import BriefIntro, ChunkContext, DossierUpdate, EnrichResult, EntityRef, Relevance, TriageResult


class StubAnalyzer:
    def triage(self, item: ItemForLLM) -> TriageResult:
        score, hits = keyword_score(item.title + "\n" + item.text[:3000])
        if score >= 8:
            rel = Relevance.core
        elif score >= 4:
            rel = Relevance.relevant
        elif score > 0:
            rel = Relevance.tangential
        else:
            rel = Relevance.off_topic
        if item.tier in ("T0", "T1") and rel == Relevance.off_topic:
            rel = Relevance.tangential
        primary = item.tier in ("T0", "T1")
        doc_type = "press_release" if primary else "news"
        return TriageResult(relevance=rel, topics=[], doc_type=doc_type, impact_type="policy_signal" if primary else "commentary",
                            primary_source=primary, reason=f"stub: keyword score {score} ({', '.join(hits[:5])})")

    def enrich(self, item: ItemForLLM) -> EnrichResult:
        inst = find_institutions(item.title + " " + item.text[:3000])
        text = item.text.strip().replace("\n", " ")
        return EnrichResult(
            summary_ko=(text[:300] or item.title), key_facts=[item.title[:120]], why_it_matters="(stub) LLM 미사용 실행",
            materiality=3 if item.tier in ("T0", "T1") else 2, materiality_reason="stub", novelty="new",
            confidence="low" if item.extract_kind in (None, "snippet", "unsupported") else "medium",
            entities=[EntityRef(name=n, type="org") for n, _ in inst[:5]], relations=[],
        )

    def brief_intro(self, items: list[BriefItemForLLM]) -> BriefIntro:
        pts = [f"{(i.entity or i.title)}: {i.title[:80]}" for i in items[:3]] or ["오늘 신규 항목 없음"]
        return BriefIntro(headline_points=pts)

    def chunk_context(self, doc_text: str, chunk: str) -> ChunkContext:
        return ChunkContext(context="")

    def dossier_update(self, title: str, previous: str, new_docs: list[BriefItemForLLM]) -> DossierUpdate:
        lines = [f"- {d.item_id}: {d.title}" for d in new_docs]
        md = (previous or f"# {title}\n") + "\n\n## 신규 (stub)\n" + "\n".join(lines) if lines else previous
        return DossierUpdate(markdown=md or f"# {title}\n", changed=bool(lines))
