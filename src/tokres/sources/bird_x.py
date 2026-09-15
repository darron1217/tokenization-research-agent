"""X/Twitter via the `bird` CLI (cookie auth). Optional; disabled when cookies or binary are missing.

Tier inheritance: posts from handles in x_accounts.yaml get that institution's tier; others are T4.
Posts that link to an external URL are also emitted as a second RawItem for the linked page (1-hop promotion)
so the primary source gets fetched and clustered with the post.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime

from ..config import domain_info, keyword_score, load_x_accounts, topic_queries
from ..models import RawItem, SourceSpec
from ..settings import get_settings
from ..urls import host_of
from ._dates import parse_date

log = logging.getLogger(__name__)
_URL = re.compile(r"https?://\S+")


def bird_available() -> bool:
    return shutil.which("bird") is not None and get_settings().has_x


def run_bird(args: list[str], timeout: int = 90) -> list[dict]:
    s = get_settings()
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp"),
           "AUTH_TOKEN": s.auth_token or "", "CT0": s.ct0 or ""}
    proc = subprocess.run(["bird", *args, "--json"], capture_output=True, text=True, timeout=timeout, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"bird {' '.join(args[:2])} failed: {proc.stderr.strip()[:300]}")
    data = json.loads(proc.stdout or "[]")
    if isinstance(data, dict):
        data = data.get("tweets") or data.get("data") or data.get("items") or []
    return data


def tweets_to_items(tweets: list[dict], source: str, since: datetime | None) -> list[RawItem]:
    accounts = load_x_accounts()
    out: list[RawItem] = []
    for t in tweets:
        text = (t.get("text") or t.get("full_text") or "").strip()
        author = t.get("author") or {}
        handle = (author.get("username") if isinstance(author, dict) else author) or t.get("username") or ""
        handle = str(handle).lower().lstrip("@")
        tid = str(t.get("id") or t.get("id_str") or "")
        if not text or not tid or not handle:
            continue
        created = parse_date(t.get("createdAt") or t.get("created_at"))
        if since and created and created < since:
            continue
        acct = accounts.get(handle)
        tier = acct["tier"] if acct else "T4"
        entity = acct["entity"] if acct else None
        # Only keep unregistered-account posts if they hit keywords (registered institutional accounts always pass)
        if not acct and keyword_score(text)[0] == 0:
            continue
        url = f"https://x.com/{handle}/status/{tid}"
        title = text.split("\n")[0][:140] or f"@{handle} post"
        links = [u.rstrip(").,") for u in _URL.findall(text) if "x.com" not in u and "t.co" not in u]
        out.append(RawItem(url=url, title=f"@{handle}: {title}", source=source, published_at=created, snippet=text,
                           tier=tier, entity=entity, region=None,
                           extra={"handle": handle, "tweet_id": tid, "links": links, "kind": "x_post"}))
        for link in links[:2]:  # 1-hop promotion of linked primary sources
            info = domain_info(host_of(link))
            if info and info["tier"] in ("T0", "T1"):
                out.append(RawItem(url=link, title=title, source=source, published_at=created, snippet=text,
                                   tier=info["tier"], entity=info.get("entity"), region=info.get("region"),
                                   extra={"promoted_from": url}))
    return out


class BirdXAdapter:
    def discover(self, spec: SourceSpec, since: datetime) -> list[RawItem]:
        if not bird_available():
            raise RuntimeError("bird CLI or AUTH_TOKEN/CT0 not available; X adapter skipped")
        mode = spec.extra.get("mode", "following")
        limit = int(spec.extra.get("limit", 100))
        tweets: list[dict] = []
        if mode == "following":
            tweets = run_bird(["home", "--following", "-n", str(limit)])
        elif mode == "search":
            for q in topic_queries(spec.extra.get("queries_from", "topics.x_queries")):
                try:
                    tweets.extend(run_bird(["search", q, "-n", str(limit)]))
                except RuntimeError as e:
                    log.warning("bird search %r failed: %s", q, e)
        return tweets_to_items(tweets, spec.name, since)
