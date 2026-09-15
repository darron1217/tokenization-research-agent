"""Agentic Q&A: Claude iteratively calls search/get/timeline tools, then answers with [item_id] citations."""
from __future__ import annotations

import anthropic

from ..db import Database
from ..llm import tools
from ..llm.analyzer import load_prompt
from ..settings import get_settings


def ask(db: Database, question: str, max_iterations: int = 12) -> str:
    s = get_settings()
    if not s.has_llm:
        return "ANTHROPIC_API_KEY not set — use `tokres search` instead."
    tools.bind(db)
    client = anthropic.Anthropic()
    runner = client.beta.messages.tool_runner(
        model=s.answer_model,
        max_tokens=8000,
        system=[{"type": "text", "text": load_prompt("ask"), "cache_control": {"type": "ephemeral"}}],
        tools=tools.ALL_TOOLS,
        messages=[{"role": "user", "content": question}],
        max_iterations=max_iterations,
    )
    final = runner.until_done()
    usage = final.usage
    db.record_usage(stage="ask", model=s.answer_model, item_id=None, input_tokens=usage.input_tokens,
                    cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
                    cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0, output_tokens=usage.output_tokens)
    return "\n".join(b.text for b in final.content if b.type == "text")
