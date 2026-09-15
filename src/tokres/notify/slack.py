from __future__ import annotations

import logging

import httpx

from ..settings import get_settings

log = logging.getLogger(__name__)


def post_slack(blocks: list[dict], text: str) -> bool:
    s = get_settings()
    if not s.has_slack:
        log.info("SLACK_WEBHOOK_URL not set; skipping Slack delivery")
        return False
    r = httpx.post(s.slack_webhook_url, json={"text": text, "blocks": blocks[:50]}, timeout=20)
    r.raise_for_status()
    return True


def brief_blocks(date: str, headline: list[str], top: list[dict], counts: dict[str, int], warnings: list[str]) -> tuple[list[dict], str]:
    text = f"토큰화 데일리 브리프 {date}"
    blocks: list[dict] = [{"type": "header", "text": {"type": "plain_text", "text": f"📌 토큰화 데일리 브리프 {date}"}}]
    if headline:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*오늘의 핵심*\n" + "\n".join(f"• {h}" for h in headline)}})
    if top:
        lines = []
        for t in top[:8]:
            tag = {"must_read": "🔴", "notable": "🟠", "fyi": "🟢"}.get(t["priority"], "⚪")
            lines.append(f"{tag} <{t['url']}|{t['title'][:90]}> — {t.get('entity') or '-'} · {t['tier']}")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})
    summary = " · ".join(f"{k} {v}" for k, v in counts.items())
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": summary or "신규 항목 없음"}]})
    if warnings:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "⚠️ " + " | ".join(warnings)[:2900]}]})
    return blocks, text


def post_warning(msg: str) -> None:
    try:
        post_slack([{"type": "section", "text": {"type": "mrkdwn", "text": f"⚠️ tokres: {msg[:2900]}"}}], f"tokres warning: {msg[:200]}")
    except Exception as e:  # noqa: BLE001
        log.warning("slack warning delivery failed: %r", e)
