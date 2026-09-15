"""Anthropic-backed analyzer using structured outputs (messages.parse) with a frozen, cached system prompt."""
from __future__ import annotations

import logging
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel

from ..config import load_topics
from ..db import Database
from ..settings import get_settings
from .analyzer import BriefItemForLLM, ItemForLLM, load_prompt, prompt_version, render_item
from .schemas import BriefIntro, ChunkContext, DossierUpdate, EnrichResult, TriageResult

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

TRIAGE_MAX_CHARS = 1500
ENRICH_MAX_CHARS = 60000


def _institution_block() -> str:
    """Deterministic rendering of the institution dictionary (sorted) to pad + stabilize the cached prefix."""
    inst = load_topics()["institutions"]
    lines = [f"- {k}: aliases={', '.join(map(str, v.get('aliases', [])))}" for k, v in sorted(inst.items())]
    return "## Institution dictionary\n" + "\n".join(lines)


class AnthropicAnalyzer:
    def __init__(self, db: Database | None = None, *, triage_model: str | None = None, enrich_model: str | None = None,
                 stage_tag: str = "daily"):
        s = get_settings()
        self.client = anthropic.Anthropic()
        self.db = db
        self.triage_model = triage_model or s.triage_model
        self.enrich_model = enrich_model or s.enrich_model
        self.answer_model = s.answer_model
        self.stage_tag = stage_tag
        # Frozen system prompts (rendered once; no volatile content)
        self.sys_triage = load_prompt("triage") + "\n\n" + _institution_block()
        self.sys_enrich = load_prompt("enrich") + "\n\n" + _institution_block()
        self.sys_brief = load_prompt("brief")
        self.sys_chunk = load_prompt("chunk_context")
        self.sys_dossier = load_prompt("dossier")
        self.versions = {n: prompt_version(n) for n in ("triage", "enrich", "brief", "chunk_context", "dossier")}

    # ---- core call ----
    def _parse(self, *, model: str, system: str, user: str, schema: type[T], max_tokens: int, stage: str,
               item_id: int | None = None, effort: str | None = None) -> T:
        kwargs: dict[str, Any] = dict(
            model=model, max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        )
        if effort and not model.startswith("claude-haiku"):
            kwargs["output_config"] = {"effort": effort}
        resp = self.client.messages.parse(**kwargs)
        u = resp.usage
        if self.db is not None:
            self.db.record_usage(stage=f"{self.stage_tag}:{stage}", model=model, item_id=item_id,
                                 input_tokens=u.input_tokens, cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
                                 cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0, output_tokens=u.output_tokens)
        if resp.parsed_output is None:
            raise RuntimeError(f"structured output missing (stop_reason={resp.stop_reason})")
        return resp.parsed_output

    # ---- public API ----
    def triage(self, item: ItemForLLM) -> TriageResult:
        return self._parse(model=self.triage_model, system=self.sys_triage, user=render_item(item, TRIAGE_MAX_CHARS),
                           schema=TriageResult, max_tokens=600, stage="triage", item_id=item.item_id)

    def enrich(self, item: ItemForLLM) -> EnrichResult:
        return self._parse(model=self.enrich_model, system=self.sys_enrich, user=render_item(item, ENRICH_MAX_CHARS),
                           schema=EnrichResult, max_tokens=3000, stage="enrich", item_id=item.item_id, effort="low")

    def brief_intro(self, items: list[BriefItemForLLM]) -> BriefIntro:
        user = "\n".join(f"- [{i.priority}] {i.entity or '-'} (tier {i.tier}) — {i.title}\n  {i.summary[:400]}" for i in items[:12])
        return self._parse(model=self.enrich_model, system=self.sys_brief, user=user or "(no items)", schema=BriefIntro,
                           max_tokens=800, stage="brief", effort="low")

    def chunk_context(self, doc_text: str, chunk: str) -> ChunkContext:
        # Document goes in the system prompt so it is cached across the document's chunks
        system = self.sys_chunk + "\n\n<document>\n" + doc_text[:150000] + "\n</document>"
        return self._parse(model=self.triage_model, system=system, user=f"<chunk>\n{chunk}\n</chunk>", schema=ChunkContext,
                           max_tokens=200, stage="chunk_context")

    def dossier_update(self, title: str, previous: str, new_docs: list[BriefItemForLLM]) -> DossierUpdate:
        docs = "\n".join(f"- [{d.item_id}] {d.title} ({d.entity or '-'})\n  {d.summary[:600]}" for d in new_docs)
        user = f"TITLE: {title}\n\nPREVIOUS DOSSIER:\n{previous or '(empty)'}\n\nNEW DOCUMENTS:\n{docs or '(none)'}"
        return self._parse(model=self.enrich_model, system=self.sys_dossier, user=user, schema=DossierUpdate,
                           max_tokens=6000, stage="dossier", effort="medium")


def make_analyzer(db: Database | None, use_llm: bool, **kw) -> Any:
    from .stub_analyzer import StubAnalyzer

    if use_llm and get_settings().has_llm:
        return AnthropicAnalyzer(db, **kw)
    if use_llm:
        log.warning("ANTHROPIC_API_KEY not set; falling back to StubAnalyzer")
    return StubAnalyzer()
