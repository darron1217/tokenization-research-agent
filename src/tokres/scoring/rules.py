"""Deterministic two-layer scoring: prior (tier/entity/doc_type/keywords/corroboration/age) × LLM materiality."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from ..config import load_scoring, load_topics
from ..db import Database, utcnow
from ..models import RunStats

PRIORITY_RANK = {"must_read": 3, "notable": 2, "fyi": 1, "archive": 0}


@dataclass
class ScoreResult:
    prior: float
    final: float
    priority: str
    components: dict


def _entity_weight(entity: str | None) -> int:
    rules = load_scoring()
    if not entity:
        return 0
    inst = load_topics()["institutions"].get(entity)
    return int(inst["weight"]) if inst else int(rules["entity_default_weight"])


def score_item(*, tier: str, entity: str | None, doc_type: str | None, keyword_score: int, corroboration: int,
               published_at: datetime | None, materiality: int | None, novelty: str | None, impact_type: str | None,
               relevance: str | None, now: datetime | None = None) -> ScoreResult:
    r = load_scoring()
    now = now or datetime.now(UTC)
    comp: dict = {}
    comp["tier"] = r["tier_weight"].get(tier, 0)
    comp["entity"] = _entity_weight(entity)
    comp["doc_type"] = r["doc_type_weight"].get(doc_type or "other", r["doc_type_weight"]["other"])
    comp["keywords"] = min(keyword_score, int(r["keyword_hits_cap"]))
    comp["corroboration"] = min(corroboration * int(r["corroboration_per_source"]), int(r["corroboration_cap"]))
    age_days = 0.0
    if published_at:
        pa = published_at if published_at.tzinfo else published_at.replace(tzinfo=UTC)
        age_days = max(0.0, (now - pa).total_seconds() / 86400)
    comp["age_penalty"] = -min(age_days * float(r["age_penalty_per_day"]), float(r["age_penalty_cap"]))
    prior = float(sum(comp.values()))
    m = materiality if materiality is not None else (3 if relevance == "core" else 2 if relevance == "relevant" else 1)
    final = prior * (0.5 + m / 5)
    th = r["priority_thresholds"]
    if novelty == "repeat" or relevance in ("off_topic",):
        priority = "archive"
    elif relevance == "tangential":
        priority = "archive" if final < th["fyi"] else "fyi"
    elif final >= th["must_read"]:
        priority = "must_read"
    elif final >= th["notable"]:
        priority = "notable"
    elif final >= th["fyi"]:
        priority = "fyi"
    else:
        priority = "archive"
    ov = r.get("must_read_override", {})
    min_m = (ov.get("min_materiality") or {}).get(impact_type, 1)
    if (tier in ov.get("tiers", []) and impact_type in ov.get("impact_types", []) and relevance in ("core", "relevant")
            and novelty != "repeat" and m >= min_m):
        priority = "must_read"
    cap = r.get("t4_uncorroborated_cap")
    if tier == "T4" and corroboration == 0 and cap and PRIORITY_RANK[priority] > PRIORITY_RANK[cap]:
        priority = cap
    comp["materiality"] = m
    return ScoreResult(prior=prior, final=round(final, 2), priority=priority, components=comp)


def compute_scores(db: Database, item_ids: list[int] | None = None, now: datetime | None = None) -> RunStats:
    stats = RunStats(stage="score")
    r = load_scoring()
    where = "i.status IN ('enriched','indexed')"
    params: list = []
    if item_ids:
        where += f" AND i.id IN ({','.join('?' * len(item_ids))})"
        params = list(item_ids)
    rows = db.q(f"""SELECT i.id, i.source_tier, i.entity, i.keyword_score, i.published_at, i.brief_date,
                           t.doc_type, t.impact_type, t.relevance, a.materiality, a.novelty,
                           (SELECT COUNT(*) FROM clusters c2 WHERE c2.cluster_id=(SELECT cluster_id FROM clusters c1 WHERE c1.item_id=i.id LIMIT 1)) AS cl
                    FROM items i LEFT JOIN triage t ON t.item_id=i.id LEFT JOIN analyses a ON a.item_id=i.id WHERE {where}""", params)
    with db.tx():
        for row in rows:
            pub = datetime.fromisoformat(row["published_at"]) if row["published_at"] else None
            # Backfilled/old items: age is measured at brief time; for archive-only scoring we freeze age at 0 so
            # historical items keep their intrinsic importance for search ranking.
            eff_now = now
            if row["brief_date"] == "backfill":
                eff_now = pub
            corro = max(0, int(row["cl"] or 1) - 1)
            res = score_item(tier=row["source_tier"], entity=row["entity"], doc_type=row["doc_type"],
                             keyword_score=row["keyword_score"] or 0, corroboration=corro, published_at=pub,
                             materiality=row["materiality"], novelty=row["novelty"], impact_type=row["impact_type"],
                             relevance=row["relevance"], now=eff_now)
            db.x("""INSERT OR REPLACE INTO scores(item_id, prior, final_score, priority, components, rule_version, computed_at)
                    VALUES (?,?,?,?,?,?,?)""",
                 (row["id"], res.prior, res.final, res.priority, json.dumps(res.components), int(r["rule_version"]), utcnow()))
            stats.ok += 1
    return stats
