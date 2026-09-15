from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

Tier = Literal["T0", "T1", "T2", "T3", "T4"]
TIER_ORDER: dict[str, int] = {"T0": 0, "T1": 1, "T2": 2, "T3": 3, "T4": 4}


class Status(StrEnum):
    discovered = "discovered"
    fetched = "fetched"
    triaged = "triaged"
    enriched = "enriched"
    indexed = "indexed"
    dropped = "dropped"
    error = "error"


class SourceSpec(BaseModel):
    name: str
    type: str
    tier: Tier = "T3"
    entity: str | None = None
    region: str = "GLOBAL"
    url: str | None = None
    poll: str = "daily"
    enabled: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, d: dict[str, Any]) -> SourceSpec:
        known = {"name", "type", "tier", "entity", "region", "url", "poll", "enabled"}
        base = {k: v for k, v in d.items() if k in known}
        extra = {k: v for k, v in d.items() if k not in known}
        return cls(**base, extra=extra)


class RawItem(BaseModel):
    """What a source adapter yields before persistence."""

    url: str
    title: str
    source: str
    published_at: datetime | None = None
    snippet: str | None = None
    tier: Tier | None = None  # override (e.g. X account inheritance / domain mapping)
    entity: str | None = None
    region: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Item(BaseModel):
    id: int
    canonical_url: str
    url: str
    source: str
    source_tier: Tier
    entity: str | None
    region: str | None
    title: str
    snippet: str | None
    published_at: datetime | None
    discovered_at: datetime
    status: Status
    lang: str | None = None
    content_text: str | None = None
    content_hash: str | None = None
    extract_kind: str | None = None
    attempts: int = 0
    last_error: str | None = None
    brief_date: str | None = None
    keyword_score: int = 0


class RunStats(BaseModel):
    stage: str
    ok: int = 0
    failed: int = 0
    notes: list[str] = Field(default_factory=list)

    def note(self, msg: str) -> None:
        self.notes.append(msg)
