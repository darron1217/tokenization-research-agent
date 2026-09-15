"""Analyzer protocol + shared input models. Implementations: AnthropicAnalyzer, StubAnalyzer."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..settings import get_settings
from .schemas import BriefIntro, ChunkContext, DossierUpdate, EnrichResult, TriageResult


@dataclass
class PriorDoc:
    item_id: int
    title: str
    published_at: str | None
    summary: str


@dataclass
class ItemForLLM:
    item_id: int
    title: str
    source: str
    tier: str
    entity: str | None
    published_at: str | None
    url: str
    text: str
    extract_kind: str | None = None
    priors: list[PriorDoc] = field(default_factory=list)


@dataclass
class BriefItemForLLM:
    item_id: int
    title: str
    entity: str | None
    tier: str
    priority: str
    summary: str


class Analyzer(Protocol):
    def triage(self, item: ItemForLLM) -> TriageResult: ...
    def enrich(self, item: ItemForLLM) -> EnrichResult: ...
    def brief_intro(self, items: list[BriefItemForLLM]) -> BriefIntro: ...
    def chunk_context(self, doc_text: str, chunk: str) -> ChunkContext: ...
    def dossier_update(self, title: str, previous: str, new_docs: list[BriefItemForLLM]) -> DossierUpdate: ...


def load_prompt(name: str) -> str:
    return (get_settings().prompts_dir / f"{name}.md").read_text(encoding="utf-8").strip()


def prompt_version(name: str) -> str:
    p: Path = get_settings().prompts_dir / f"{name}.md"
    return hashlib.sha1(p.read_bytes()).hexdigest()[:10]


def render_item(item: ItemForLLM, max_chars: int) -> str:
    """Volatile user-turn content (never goes in the system prompt)."""
    head = (f"item_id: {item.item_id}\ntitle: {item.title}\nsource: {item.source} (tier {item.tier})\n"
            f"entity: {item.entity or '-'}\npublished: {item.published_at or '-'}\nurl: {item.url}\n"
            f"text_kind: {item.extract_kind or '-'}\n")
    body = item.text[:max_chars]
    if len(item.text) > max_chars:
        body += f"\n[... truncated {len(item.text) - max_chars} chars]"
    priors = ""
    if item.priors:
        priors = "\n\nPRIOR DOCUMENTS (for novelty judgement):\n" + "\n".join(
            f"- [{p.item_id}] {p.published_at or '-'} {p.title}: {p.summary[:300]}" for p in item.priors)
    return f"{head}\n---\n{body}{priors}"
