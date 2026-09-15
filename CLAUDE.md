# tokres — Tokenization Research Agent

Daily pipeline that monitors asset-tokenization policy/adoption news from Korean and global institutions, judges importance,
writes a Korean brief, and indexes everything for semantic search (CLI + MCP server for chat LLMs).

## Non-negotiable decisions (from the design session)
- **Source hierarchy drives judgement.** T0 regulator/central-bank primary · T1 FMI/institution official · T2 major press ·
  T3 analysis/opinion · T4 unverified social. Defined per source in `config/sources.yaml`, per domain in `config/domains.yaml`.
- **X is tiered per account, not per platform.** Handles in `config/x_accounts.yaml` inherit their institution's tier
  (e.g. @BIS_org = T0). Only unregistered accounts are T4 (capped at `fyi` unless corroborated).
- **Two-layer scoring.** Deterministic prior (tier, entity, doc_type, keywords, corroboration, age; `config/scoring.yaml`)
  × LLM materiality/novelty (`src/tokres/llm/prompts/enrich.md`). Rules are versioned (`scores.rule_version`); `tokres score` recomputes.
- **Lightweight RAG, no graph DB.** Qdrant hybrid (dense + Korean-morpheme BM25, RRF) + SQLite entities/relations +
  living dossiers + agentic `ask`. Add graph-style expansion only if `tokres eval` shows multi-hop recall failing.
- **Source discovery proposes, humans approve.** `tokres discover-sources` writes a candidates report; nothing is added
  to config until `tokres apply-candidates` on checked lines.
- **SQLite is the source of truth; Qdrant is derived** (`tokres index --rebuild`). Portability = `git clone` + `.env` + `docker compose up -d`.
- **Everything runs without keys** (`--no-llm`, StubAnalyzer, HashEmbedder) so tests and dry-runs need no network/API.
- LLMs: `claude-haiku-4-5` for triage, `claude-opus-5` for enrich/brief/ask (`messages.parse` + Pydantic, cached system prompts).
- bird (X CLI) is an optional, isolated adapter: unofficial API, may break, cookie auth only via `.env` (`AUTH_TOKEN`, `CT0`).

## Layout
`src/tokres/` — `cli.py` (all commands) · `sources/` (adapters + backfill) · `extract.py` · `llm/` (schemas, prompts, analyzers)
· `scoring/` · `brief/` · `index/` (chunk, embed, qdrant, search) · `knowledge/` (api, entities, dossiers, ask, evals)
· `mcp/server.py` · `discovery/` · `backfill.py` · `pipeline.py`

## Commands
```bash
uv run pytest -q                       # no network / keys needed
uv run tokres doctor                   # env + Qdrant + DB check
uv run tokres run-daily [--no-llm] [--no-slack] [--no-index]
uv run tokres backfill --source fsc_press --from 2024-01-01 --dry-run
uv run tokres search "예금토큰 BIS" --tier T0,T1 ; uv run tokres ask "..."
uv run tokres-mcp                      # stdio MCP server (see .mcp.json)
docker compose up -d                   # qdrant + scheduler (07:00 KST daily) + mcp (http :8765)
```

## Gotchas
- Google News RSS links are opaque redirects (unresolvable without a browser as of 2026-09) → adapter disabled; use Naver + GDELT.
- GDELT throttles hard (≥5 s/call, 429 for minutes after bursts) → keep `gdelt_queries_*` short.
- Korean regulator pages often carry the body in a PDF/HWP attachment; `extract.py` follows PDF attachments, HWP is unsupported.
- Local embedder is `intfloat/multilingual-e5-large` via fastembed (`EMBED_MODEL=e5-large`); default is `voyage-4-lite`.
  Collections are named per model — never mix embedders in one collection.
- mcp SDK is 2.x (`mcp.server.mcpserver.MCPServer`), anthropic SDK 1.x (`messages.parse`, adaptive thinking, no `budget_tokens`).
