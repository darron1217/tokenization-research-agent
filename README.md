# tokres — 토큰화 리서치 에이전트

국내(금융위·금감원·한은·예탁원)와 해외(BIS·IIF·DTCC·SEC·FSB·IOSCO·ECB·Fed·HKMA·주요 금융회사) 기관의
**자산 토큰화 정책·기술채택 소식**을 매일 수집 → 위계·중요도 판단 → 한국어 브리프(Markdown + Slack) → 벡터 인덱싱(시맨틱 검색·MCP)까지 자동화합니다.

```
discover ─ fetch/extract ─ keyword gate ─ triage(Haiku) ─ enrich(Opus, 유사 과거문서 참조) ─ score ─ brief ─ index
                                                                                  ▲
search / ask (CLI)  ◄──── Qdrant hybrid(dense + 한국어 BM25) + SQLite entities/relations + dossiers ────►  MCP server (chat LLM 도구)
```

## 빠른 시작 (Docker, 이식성 기준)

```bash
git clone <repo> && cd tokenization-research-agent
cp .env.example .env            # ANTHROPIC_API_KEY 필수, 나머지는 선택
docker compose up -d            # qdrant + scheduler(매일 07:00 KST) + mcp(http://127.0.0.1:8765/mcp)
docker compose run --rm worker doctor
docker compose run --rm worker run-daily --limit 20     # 첫 실행
```

호스트에서 직접 실행하려면 `brew install uv && uv sync --extra dev` 후 `uv run tokres ...` (Qdrant는 Compose로 띄움).

## 키 / 설정 (`.env`)

| 변수 | 필수 | 용도 |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | triage(Haiku 4.5) · enrich/brief/ask(Opus 5). 없으면 `--no-llm` 스텁으로만 동작 |
| `EMBED_MODEL` | ✅ | `voyage-4-lite`(권장, `VOYAGE_API_KEY` 필요) 또는 `e5-large`(로컬 ONNX, 키 불필요, 첫 실행 시 ~2GB 다운로드) |
| `NAVER_CLIENT_ID/SECRET` | 선택 | 국내 뉴스 디스커버리 (developers.naver.com, 무료 25,000회/일) |
| `SLACK_WEBHOOK_URL` | 선택 | 브리프·경고 전송 |
| `AUTH_TOKEN`, `CT0` | 선택 | X 수집(bird CLI). 로그인된 x.com 브라우저 쿠키 값. 비공식 API — 계정 리스크 있음 |
| `HTTP_USER_AGENT` | 권장 | sec.gov 등은 연락처 포함 UA 요구 |

소스는 `config/sources.yaml`, 도메인→위계 매핑은 `config/domains.yaml`, 키워드·기관사전·검색쿼리는 `config/topics.yaml`,
점수 규칙은 `config/scoring.yaml`, X 계정 위계는 `config/x_accounts.yaml`. 코드 수정 없이 편집 가능합니다.

## 정보 위계와 중요도

| Tier | 정의 | 취급 |
|---|---|---|
| T0 | 규제기관·중앙은행·국제기구 원문 | 키워드 게이트 면제, 단독 must_read 가능 |
| T1 | 시장인프라·기관 공식(예탁원, DTCC, IIF, 은행 뉴스룸) | T0와 동일, 가중치 낮음 |
| T2 | 주요 언론 | 원문이 있으면 클러스터의 "보도"로 종속 |
| T3 | 분석·의견 | 인덱싱, 브리프에선 notable 이하 |
| T4 | 미등록 X 계정 | 단독으론 fyi 상한 (교차확인 시 해제) |

**X는 계정 단위로 위계를 상속**합니다(`x_accounts.yaml`의 @BIS_org = T0). 포스트가 링크한 T0/T1 원문은 자동 수집·클러스터링됩니다.

우선순위 = 결정적 prior(위계·기관·문서유형·키워드·교차확인·최신성) × LLM materiality(1–5) → `must_read / notable / fyi / archive`.
`tokres rate <id> up|down` 로 피드백을 남기면 few-shot에 반영됩니다.

## 주요 명령

```bash
uv run tokres discover [--source fsc_press] [--tiers T0,T1] [--since 3d]
uv run tokres backfill --from 2024-01-01 [--source fsc_press] [--dry-run] [--max-batches N]   # 재개 가능
uv run tokres fetch | triage | enrich [--max 25] | score | brief [--no-slack] | index [--rebuild]
uv run tokres run-daily [--no-llm] [--no-slack] [--no-index]
uv run tokres search "예금토큰 BIS" --tier T0,T1 --since 2025-01-01
uv run tokres ask "BIS와 한국은행의 예금토큰 입장 변화를 비교해줘"
uv run tokres dossiers            # 기관·프로젝트·토픽별 living dossier 갱신 (주간)
uv run tokres discover-sources    # 소스 후보 리포트 → 체크 후 tokres apply-candidates <file>
uv run tokres eval                # evals/questions.yaml 기반 recall@k
uv run tokres stats | doctor | export | import
```

## MCP (채팅 LLM에서 검색 도구로 사용)

- Claude Code: 저장소의 `.mcp.json`이 `uv run tokres-mcp`(stdio)를 등록합니다.
- Claude Desktop: `claude_desktop_config.json`에
  `{"mcpServers": {"tokres": {"command": "uv", "args": ["run", "--project", "/path/to/repo", "tokres-mcp"]}}}`
- 원격/HTTP: Compose의 `mcp` 서비스 `http://127.0.0.1:8765/mcp` (streamable HTTP).

도구: `search_documents`, `get_document`, `entity_timeline`, `list_entities`, `get_dossier`, `list_dossiers`, `recent_briefs`, `rate_document`.
리소스: `brief://YYYY-MM-DD`, `dossier://slug`. 프롬프트: `weekly_review`, `compare_positions`.

## 백필 (최근 2–3년 공식 문서)

```bash
uv run tokres backfill --tiers T0,T1 --from 2023-09-01 --dry-run --max-batches 2   # 페이지네이션 확인
uv run tokres backfill --tiers T0,T1 --from 2023-09-01                              # 수집 (중단 후 재실행하면 이어서)
uv run tokres fetch && uv run tokres triage && uv run tokres enrich --max 500 --model claude-sonnet-5
uv run tokres score && uv run tokres index && uv run tokres dossiers --rebuild
```
백필 항목은 브리프에 포함되지 않습니다(`brief_date='backfill'`). RSS 전용 소스는 `sources.yaml`의 `backfill.url`(아카이브 목록)이 있어야 합니다.

## 이전 / 백업

- 진실 소스는 `data/state.db`(SQLite). Qdrant는 `tokres index --rebuild`로 언제든 재생성.
- 이전: `data/` 디렉터리 복사, 또는 `tokres export` → 새 환경에서 `tokres import` 후 `index --rebuild`.
- `reports/`, `dossiers/`, `config/`는 git으로 관리.

## 테스트

```bash
uv run pytest -q     # 네트워크·API 키 불필요 (fixture, StubAnalyzer, HashEmbedder, in-memory Qdrant)
```

## 알려진 제약

- Google News RSS 링크는 브라우저 없이 원문 URL 해제가 불가(2026-09 기준) → 기본 비활성. Naver API + GDELT 사용.
- GDELT는 호출당 5초 이상 간격 필요, 과다 호출 시 수 분간 429.
- 예탁결제원 사이트는 비브라우저 요청을 차단 → 뉴스 우회(Playwright 어댑터는 추후).
- HWP 첨부는 본문 추출 미지원(제목·요약문으로 트리아지). PDF는 지원.
- bird(X)는 비공식 GraphQL 사용 — 언제든 깨질 수 있고 어댑터만 실패로 격리됨.
- LLM 배치 API(50% 할인) 백필 모드는 미구현 — 대량 백필 시 `--model claude-sonnet-5`로 비용 절감 권장.
