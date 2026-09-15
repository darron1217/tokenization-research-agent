"""Weekly source-discovery routine. Proposes candidates (never auto-applies): citation mining, domain frequency,
coverage gaps, query expansion, topic drift. Output: reports/source-candidates-YYYY-Www.md + source_candidates table."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import yaml

from ..config import domain_info, load_topics
from ..db import Database, utcnow
from ..llm.analyzer import Analyzer
from ..ratelimit import PoliteClient
from ..settings import get_settings
from ..urls import host_of

_URL = re.compile(r"https?://[^\s)\]>\"']+")


def _add(db: Database, kind: str, value: str, evidence: dict) -> None:
    db.x("""INSERT INTO source_candidates(kind, value, evidence, first_seen, status) VALUES (?,?,?,?,'proposed')
            ON CONFLICT(kind, value) DO UPDATE SET evidence=excluded.evidence""",
         (kind, value, json.dumps(evidence, ensure_ascii=False)[:4000], utcnow()))


def citation_mining(db: Database, days: int = 30) -> dict[str, dict]:
    """External domains linked from T0/T1 documents that are not in domains.yaml."""
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    found: dict[str, dict] = defaultdict(lambda: {"count": 0, "examples": []})
    for r in db.q("SELECT id, url, content_text FROM items WHERE source_tier IN ('T0','T1') AND discovered_at >= ? AND content_text IS NOT NULL", (since,)):
        own = host_of(r["url"])
        for u in set(_URL.findall(r["content_text"] or "")):
            h = host_of(u)
            if not h or h == own or h.endswith(own) or domain_info(h) or any(x in h for x in ("twitter.com", "x.com", "linkedin", "youtube", "facebook", "google", "w3.org")):
                continue
            found[h]["count"] += 1
            if len(found[h]["examples"]) < 3:
                found[h]["examples"].append({"item_id": r["id"], "url": u})
    return {h: v for h, v in found.items() if v["count"] >= 2}


def domain_frequency(db: Database, days: int = 30, min_count: int = 3) -> dict[str, dict]:
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    c: Counter = Counter()
    ex: dict[str, list] = defaultdict(list)
    for r in db.q("""SELECT i.id, i.url FROM items i JOIN triage t ON t.item_id=i.id
                     WHERE i.discovered_at >= ? AND t.relevance IN ('core','relevant')""", (since,)):
        h = host_of(r["url"])
        if h and not domain_info(h):
            c[h] += 1
            if len(ex[h]) < 3:
                ex[h].append(r["id"])
    return {h: {"count": n, "examples": ex[h]} for h, n in c.items() if n >= min_count}


def detect_rss(url: str) -> list[str]:
    try:
        with PoliteClient() as cl:
            r = cl.get(url, timeout=15)
            from selectolax.parser import HTMLParser

            tree = HTMLParser(r.text)
            out = []
            for lnk in tree.css("link[rel='alternate']"):
                t = (lnk.attributes.get("type") or "").lower()
                if "rss" in t or "atom" in t:
                    href = lnk.attributes.get("href") or ""
                    if href.startswith("/"):
                        p = urlparse(url)
                        href = f"{p.scheme}://{p.netloc}{href}"
                    out.append(href)
            return out
    except Exception:  # noqa: BLE001
        return []


def coverage_gaps(db: Database, days: int = 30) -> list[str]:
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    seen = {r["entity"] for r in db.q("SELECT DISTINCT entity FROM items WHERE discovered_at >= ? AND entity IS NOT NULL", (since,))}
    mentioned = set()
    for r in db.q("SELECT canonical FROM entities WHERE type='org'"):
        mentioned.add(r["canonical"])
    return [k for k in load_topics()["institutions"] if k not in seen]


def query_expansion(db: Database, analyzer: Analyzer, days: int = 14) -> list[str]:
    """Ask the LLM for new search queries/handles given the week's notable entities and facts (stub returns projects)."""
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    rows = db.q("""SELECT i.id, i.title, i.entity, i.source_tier, s.priority, a.summary_ko, a.entities FROM items i
                   JOIN scores s ON s.item_id=i.id JOIN analyses a ON a.item_id=i.id
                   WHERE i.discovered_at >= ? AND s.priority IN ('must_read','notable') ORDER BY s.final_score DESC LIMIT 30""", (since,))
    known = {q.lower() for k in ("discovery_queries_ko", "discovery_queries_en", "x_queries") for q in load_topics().get(k, [])}
    projects: Counter = Counter()
    for r in rows:
        for e in json.loads(r["entities"] or "[]"):
            if e.get("type") in ("project", "product", "regulation") and e["name"].lower() not in known:
                projects[e["name"]] += 1
    return [p for p, n in projects.most_common(20)]


def topic_drift(db: Database, days: int = 30) -> list[dict]:
    """Relevant items whose triage topics are empty → candidate new topics (cheap proxy for embedding clustering)."""
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    rows = db.q("""SELECT i.id, i.title FROM items i JOIN triage t ON t.item_id=i.id
                   WHERE i.discovered_at >= ? AND t.relevance IN ('core','relevant') AND (t.topics='[]' OR t.topics IS NULL) LIMIT 30""", (since,))
    return [{"item_id": r["id"], "title": r["title"]} for r in rows]


def discover_sources(db: Database, analyzer: Analyzer, dry_run: bool = False) -> Path:
    cites = citation_mining(db)
    doms = domain_frequency(db)
    gaps = coverage_gaps(db)
    queries = query_expansion(db, analyzer)
    drift = topic_drift(db)
    rss_hits: dict[str, list[str]] = {}
    for h in list({**cites, **doms})[:25]:
        feeds = detect_rss(f"https://{h}/")
        if feeds:
            rss_hits[h] = feeds
    if not dry_run:
        with db.tx():
            for h, v in cites.items():
                _add(db, "domain", h, {"via": "citation", **v, "rss": rss_hits.get(h, [])})
            for h, v in doms.items():
                _add(db, "domain", h, {"via": "frequency", **v, "rss": rss_hits.get(h, [])})
            for q in queries:
                _add(db, "query", q, {"via": "query_expansion"})
            for g in gaps:
                _add(db, "gap", g, {"via": "coverage_gap"})
    wk = date.today().isocalendar()
    out = get_settings().reports_dir / f"source-candidates-{wk[0]}-W{wk[1]:02d}.md"
    lines = [f"# 소스 후보 리포트 {wk[0]}-W{wk[1]:02d}", "", "체크박스를 선택한 뒤 `tokres apply-candidates <this file>` 실행 시 config에 병합됩니다.", ""]
    lines.append("## 인용 채굴 (T0/T1 문서가 링크한 미등록 도메인)")
    for h, v in sorted(cites.items(), key=lambda kv: -kv[1]["count"]):
        lines.append(f"- [ ] domain: `{h}` ({v['count']}회) rss={rss_hits.get(h, [])} 예: {', '.join('#' + str(e['item_id']) for e in v['examples'])}")
    lines.append("\n## 도메인 빈도 (relevant 이상 뉴스 발견 도메인)")
    for h, v in sorted(doms.items(), key=lambda kv: -kv[1]["count"]):
        lines.append(f"- [ ] domain: `{h}` ({v['count']}건) rss={rss_hits.get(h, [])} 예: {', '.join('#' + str(x) for x in v['examples'])}")
    lines.append("\n## 커버리지 갭 (30일간 0건인 감시 기관)")
    for g in gaps:
        lines.append(f"- [ ] gap: `{g}`")
    lines.append("\n## 쿼리 확장 후보 (신규 프로젝트·제도명)")
    for q in queries:
        lines.append(f"- [ ] query: `{q}`")
    lines.append("\n## 토픽 드리프트 (기존 토픽에 매핑되지 않은 관련 문서)")
    for d in drift:
        lines.append(f"- #{d['item_id']} {d['title']}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def apply_candidates(db: Database, report: str) -> str:
    """Merge checked lines ([x]) into config: domain→domains.yaml (T3 default), query→topics.yaml discovery queries."""
    text = Path(report).read_text(encoding="utf-8")
    cfg = get_settings().config_dir
    domains_p, topics_p = cfg / "domains.yaml", cfg / "topics.yaml"
    domains = yaml.safe_load(domains_p.read_text(encoding="utf-8"))
    topics = yaml.safe_load(topics_p.read_text(encoding="utf-8"))
    n = 0
    for m in re.finditer(r"- \[x\] (domain|query|gap): `([^`]+)`", text, flags=re.IGNORECASE):
        kind, value = m.group(1).lower(), m.group(2)
        if kind == "domain" and value not in domains["domains"]:
            domains["domains"][value] = {"tier": "T3", "entity": value, "region": "GLOBAL"}
            n += 1
        elif kind == "query":
            key = "discovery_queries_ko" if re.search(r"[가-힣]", value) else "discovery_queries_en"
            if value not in topics[key]:
                topics[key].append(value)
                n += 1
        with db.tx():
            db.x("UPDATE source_candidates SET status='approved' WHERE kind=? AND value=?", (kind, value))
    domains_p.write_text(yaml.safe_dump(domains, allow_unicode=True, sort_keys=False), encoding="utf-8")
    topics_p.write_text(yaml.safe_dump(topics, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return f"applied {n} candidate(s) to config/"
