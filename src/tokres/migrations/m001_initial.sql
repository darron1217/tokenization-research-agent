CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  canonical_url TEXT UNIQUE NOT NULL,
  url TEXT NOT NULL,
  source TEXT NOT NULL,
  source_tier TEXT NOT NULL DEFAULT 'T3',
  entity TEXT,
  region TEXT,
  title TEXT NOT NULL,
  snippet TEXT,
  published_at TEXT,
  discovered_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'discovered',
  lang TEXT,
  content_text TEXT,
  content_hash TEXT,
  extract_kind TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  brief_date TEXT,
  keyword_score INTEGER NOT NULL DEFAULT 0,
  extra_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source);
CREATE INDEX IF NOT EXISTS idx_items_brief ON items(brief_date);

CREATE TABLE IF NOT EXISTS triage (
  item_id INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
  relevance TEXT NOT NULL,
  topics TEXT NOT NULL,
  doc_type TEXT NOT NULL,
  impact_type TEXT,
  primary_source INTEGER NOT NULL DEFAULT 0,
  reason TEXT,
  model TEXT,
  prompt_version TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
  item_id INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
  summary_ko TEXT NOT NULL,
  key_facts TEXT NOT NULL,
  why_it_matters TEXT,
  what_changed TEXT,
  materiality INTEGER NOT NULL,
  materiality_reason TEXT,
  novelty TEXT NOT NULL,
  updates_item_id INTEGER,
  confidence TEXT NOT NULL,
  entities TEXT,
  relations TEXT,
  effective_date TEXT,
  model TEXT,
  prompt_version TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scores (
  item_id INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
  prior REAL NOT NULL,
  final_score REAL NOT NULL,
  priority TEXT NOT NULL,
  components TEXT,
  rule_version INTEGER NOT NULL,
  computed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scores_priority ON scores(priority);

CREATE TABLE IF NOT EXISTS clusters (
  cluster_id TEXT NOT NULL,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  is_head INTEGER NOT NULL DEFAULT 0,
  brief_date TEXT,
  PRIMARY KEY (cluster_id, item_id)
);

CREATE TABLE IF NOT EXISTS entities (
  id INTEGER PRIMARY KEY,
  canonical TEXT UNIQUE NOT NULL,
  type TEXT NOT NULL,
  aliases TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS item_entities (
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  role TEXT,
  PRIMARY KEY (item_id, entity_id)
);
CREATE TABLE IF NOT EXISTS relations (
  id INTEGER PRIMARY KEY,
  subject_id INTEGER NOT NULL REFERENCES entities(id),
  predicate TEXT NOT NULL,
  object_id INTEGER NOT NULL REFERENCES entities(id),
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  date TEXT,
  UNIQUE (subject_id, predicate, object_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_relations_subject ON relations(subject_id);
CREATE INDEX IF NOT EXISTS idx_relations_object ON relations(object_id);

CREATE TABLE IF NOT EXISTS dossiers (
  slug TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  updated_at TEXT,
  last_item_id INTEGER
);

CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  rating TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_candidates (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  value TEXT NOT NULL,
  evidence TEXT,
  first_seen TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'proposed',
  UNIQUE (kind, value)
);

CREATE TABLE IF NOT EXISTS backfill_progress (
  source TEXT NOT NULL,
  window_from TEXT NOT NULL,
  window_to TEXT NOT NULL,
  cursor TEXT,
  status TEXT NOT NULL DEFAULT 'running',
  fetched INTEGER NOT NULL DEFAULT 0,
  done_at TEXT,
  PRIMARY KEY (source, window_from, window_to)
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  stage TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  ok INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS llm_usage (
  id INTEGER PRIMARY KEY,
  run_id INTEGER,
  stage TEXT NOT NULL,
  model TEXT NOT NULL,
  item_id INTEGER,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0,
  cache_write_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deliveries (
  brief_date TEXT NOT NULL,
  channel TEXT NOT NULL,
  sent_at TEXT NOT NULL,
  PRIMARY KEY (brief_date, channel)
);

CREATE TABLE IF NOT EXISTS source_health (
  source TEXT PRIMARY KEY,
  last_ok_at TEXT,
  last_error_at TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);
