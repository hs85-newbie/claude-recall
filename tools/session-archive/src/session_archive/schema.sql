-- ADR-001 L1 스키마 (~/.claude-archive/sessions.db)
-- 변경 시 schema_version 테이블 갱신 + 마이그레이션 필수

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  session_id           TEXT PRIMARY KEY,
  project_dir          TEXT NOT NULL,
  project_slug         TEXT NOT NULL,
  started_at           TEXT NOT NULL,
  ended_at             TEXT,
  event_count          INTEGER NOT NULL DEFAULT 0,
  user_turn_count      INTEGER NOT NULL DEFAULT 0,
  assistant_turn_count INTEGER NOT NULL DEFAULT 0,
  git_branch           TEXT,
  source_file          TEXT NOT NULL,
  source_mtime         REAL NOT NULL,
  source_last_uuid     TEXT,
  promoted_to_l2       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sessions_project
  ON sessions(project_slug, started_at DESC);

-- NOTE: PK는 (session_id, uuid) 복합키.
-- 서브에이전트가 parent와 이벤트 UUID를 공유하는 경우를 고려.
CREATE TABLE IF NOT EXISTS events (
  session_id   TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
  uuid         TEXT NOT NULL,
  parent_uuid  TEXT,
  type         TEXT NOT NULL,
  timestamp    TEXT NOT NULL,
  role         TEXT,
  content      TEXT,
  content_hash TEXT,
  tool_name    TEXT,
  cwd          TEXT,
  git_branch   TEXT,
  masked       INTEGER NOT NULL DEFAULT 0,
  token_count  INTEGER,
  PRIMARY KEY (session_id, uuid)
);

CREATE INDEX IF NOT EXISTS idx_events_session
  ON events(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type
  ON events(type);

CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
  uuid UNINDEXED,
  session_id UNINDEXED,
  content,
  tokenize = 'unicode61'
);

-- file-history-snapshot은 uuid가 없고 messageId 기준으로 식별된다.
CREATE TABLE IF NOT EXISTS file_snapshots (
  session_id       TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
  message_id       TEXT NOT NULL,
  timestamp        TEXT,
  is_update        INTEGER NOT NULL DEFAULT 0,
  tracked_count    INTEGER NOT NULL DEFAULT 0,
  file_paths_json  TEXT,
  PRIMARY KEY (session_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_session
  ON file_snapshots(session_id, timestamp);

CREATE TABLE IF NOT EXISTS parse_errors (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  source_file TEXT NOT NULL,
  line_no     INTEGER NOT NULL,
  error       TEXT NOT NULL,
  raw         TEXT,
  seen_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mask_stats (
  session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
  category   TEXT NOT NULL,
  hits       INTEGER NOT NULL,
  PRIMARY KEY (session_id, category)
);

-- L2 요약: ADR-001 Phase 2
-- intent/outcome/decisions는 Haiku(or Sonnet 재시도) 응답을 정규화해 저장
CREATE TABLE IF NOT EXISTS session_summaries (
  session_id           TEXT PRIMARY KEY REFERENCES sessions(session_id) ON DELETE CASCADE,
  intent               TEXT NOT NULL,
  outcome              TEXT,
  decisions_json       TEXT,
  tags_json            TEXT,
  related_commits_json TEXT,
  files_touched_json   TEXT,
  model                TEXT NOT NULL,
  input_tokens         INTEGER,
  output_tokens        INTEGER,
  summary_cost_usd     REAL,
  summarized_at        TEXT NOT NULL,
  quality_score        INTEGER
);

CREATE INDEX IF NOT EXISTS idx_summaries_quality
  ON session_summaries(quality_score);

-- L2 실패 로그: JSON 파싱 실패/5xx/timeout 누적
CREATE TABLE IF NOT EXISTS summarize_errors (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  TEXT NOT NULL,
  model       TEXT,
  error_kind  TEXT NOT NULL,
  error       TEXT NOT NULL,
  seen_at     TEXT NOT NULL
);

-- L2 예산 트래킹 (일자별)
CREATE TABLE IF NOT EXISTS summarize_budget (
  day_utc       TEXT PRIMARY KEY,
  input_tokens  INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd      REAL NOT NULL DEFAULT 0,
  call_count    INTEGER NOT NULL DEFAULT 0,
  opus_calls    INTEGER NOT NULL DEFAULT 0
);

-- L2 C안: Gemma4 ∥ Haiku 병렬 + Sonnet/Opus 승급 체인.
-- 호출된 모든 모델의 파싱 결과 + 4축 점수를 전부 저장, chosen=1 이 session_summaries 승자.
CREATE TABLE IF NOT EXISTS summary_candidates (
  session_id       TEXT NOT NULL,
  model            TEXT NOT NULL,
  parsed_json      TEXT,
  schema_score     INTEGER NOT NULL,
  richness_score   INTEGER NOT NULL,
  grounding_score  INTEGER NOT NULL,
  self_quality     INTEGER NOT NULL,
  composite        INTEGER NOT NULL,
  chosen           INTEGER NOT NULL DEFAULT 0,
  input_tokens     INTEGER,
  output_tokens    INTEGER,
  cost_usd         REAL,
  latency_ms       INTEGER,
  error            TEXT,
  created_at       TEXT NOT NULL,
  PRIMARY KEY (session_id, model)
);

CREATE INDEX IF NOT EXISTS idx_candidates_session
  ON summary_candidates(session_id, composite DESC);

-- gstack /context-save 체크포인트 적재 (~/.gstack/projects/<slug>/checkpoints/*.md).
-- 세션 요약과 별개 네임스페이스 — "다음 할 일/인계" 정보의 단기 RAG 소스.
CREATE TABLE IF NOT EXISTS checkpoints (
  checkpoint_id   TEXT PRIMARY KEY,   -- machine::slug::filename (멱등 키)
  machine         TEXT,
  project_slug    TEXT,
  title           TEXT,
  content         TEXT,               -- 마스킹된 본문
  created_at      TEXT,               -- 파일명 타임스탬프(YYYYMMDD-HHMMSS) 파싱
  source_file     TEXT NOT NULL,
  source_mtime    REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_created
  ON checkpoints(created_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS checkpoints_fts USING fts5(
  checkpoint_id UNINDEXED,
  content,
  tokenize = 'unicode61'
);
