"""Pydantic schemas for structured LLM outputs. Keep field order stable (schema is part of the cached prefix)."""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Relevance(StrEnum):
    off_topic = "off_topic"
    tangential = "tangential"
    relevant = "relevant"
    core = "core"


DocType = Literal["regulation", "guideline", "press_release", "report", "speech", "news", "opinion", "other"]
ImpactType = Literal["regulatory_change", "policy_signal", "infrastructure_launch", "pilot_or_poc", "research", "commentary"]
Predicate = Literal["announces", "regulates", "participates_in", "pilots", "launches", "cites", "updates", "partners_with"]
Topic = Literal[
    "tokenized_securities", "tokenized_deposits", "stablecoin", "cbdc", "tokenized_funds_rwa",
    "dlt_settlement", "custody_infra", "regulation_legal", "cross_border", "interoperability",
]


class TriageResult(BaseModel):
    relevance: Relevance
    topics: list[Topic] = Field(default_factory=list)
    doc_type: DocType
    impact_type: ImpactType
    primary_source: bool = Field(description="True if this document is the original announcement/paper, not coverage of it")
    reason: str = Field(max_length=300)


class EntityRef(BaseModel):
    name: str
    type: Literal["org", "project", "regulation", "product"]


class Relation(BaseModel):
    subject: str
    predicate: Predicate
    object: str


class EnrichResult(BaseModel):
    summary_ko: str = Field(description="3 sentences in Korean")
    key_facts: list[str] = Field(max_length=5)
    why_it_matters: str = Field(description="Korean; impact on Korean tokenization market/regulation and global direction")
    what_changed: str | None = Field(default=None, description="If novelty=update: what changed vs the prior document")
    materiality: int = Field(ge=1, le=5)
    materiality_reason: str = Field(max_length=300)
    novelty: Literal["new", "update", "repeat"]
    updates_item_id: int | None = Field(default=None, description="item_id of the prior document this updates/repeats")
    confidence: Literal["high", "medium", "low"]
    entities: list[EntityRef] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    effective_date: str | None = Field(default=None, description="ISO date if the document sets an effective/deadline date")


class BriefIntro(BaseModel):
    headline_points: list[str] = Field(min_length=1, max_length=3, description="오늘의 핵심 (Korean, one sentence each)")


class ChunkContext(BaseModel):
    context: str = Field(max_length=300, description="One sentence situating this chunk within the document")


class DossierUpdate(BaseModel):
    markdown: str
    changed: bool
