"""Loaders for config/*.yaml."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml

from .models import SourceSpec
from .settings import get_settings


def _load(name: str) -> Any:
    p = get_settings().config_dir / name
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache
def load_sources() -> list[SourceSpec]:
    return [SourceSpec.from_yaml(d) for d in _load("sources.yaml")]


@lru_cache
def load_domains() -> dict[str, dict[str, Any]]:
    return _load("domains.yaml")["domains"]


@lru_cache
def load_topics() -> dict[str, Any]:
    return _load("topics.yaml")


@lru_cache
def load_scoring() -> dict[str, Any]:
    return _load("scoring.yaml")


@lru_cache
def load_x_accounts() -> dict[str, dict[str, Any]]:
    data = _load("x_accounts.yaml")["accounts"]
    return {a["handle"].lower().lstrip("@"): a for a in data}


def domain_info(host: str) -> dict[str, Any] | None:
    host = host.lower()
    best = None
    for dom, info in load_domains().items():
        if host == dom or host.endswith("." + dom):
            if best is None or len(dom) > len(best[0]):
                best = (dom, info)
    return best[1] if best else None


def topic_queries(key: str) -> list[str]:
    """Resolve 'topics.discovery_queries_ko' style refs."""
    _, _, field = key.partition(".")
    return list(load_topics().get(field, []))


def keyword_score(text: str) -> tuple[int, list[str]]:
    """Return (weighted score capped later by scoring rules, matched terms)."""
    if not text:
        return 0, []
    low = text.lower()
    total = 0
    hits: list[str] = []
    for lang in ("ko", "en"):
        for kw in load_topics()["keywords"][lang]:
            term = kw["term"].lower()
            if term in low:
                total += int(kw["weight"])
                hits.append(kw["term"])
    return total, hits


def institution_lookup() -> dict[str, tuple[str, int]]:
    """alias(lower) -> (canonical, weight)"""
    out: dict[str, tuple[str, int]] = {}
    for canon, info in load_topics()["institutions"].items():
        out[canon.lower()] = (canon, int(info.get("weight", 5)))
        for a in info.get("aliases", []):
            out[str(a).lower()] = (canon, int(info.get("weight", 5)))
    return out


def find_institutions(text: str) -> list[tuple[str, int]]:
    if not text:
        return []
    low = text.lower()
    found: dict[str, int] = {}
    for alias, (canon, w) in institution_lookup().items():
        if len(alias) < 3:
            continue
        if alias in low:
            found[canon] = max(found.get(canon, 0), w)
    return sorted(found.items(), key=lambda kv: -kv[1])
