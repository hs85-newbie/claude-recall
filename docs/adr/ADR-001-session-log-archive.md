# ADR-001: Claude Code 세션 로그 아카이브 파이프라인

- **상태**: Accepted
- **결정일**: 2026-04-15
- **승인일**: 2026-04-15
- **결정권자**: you
- **관련 세션**: session-log-archive
- **후속 ADR**: (L3 CKG 단계 진입 시 ADR-002로 분리 예정)

---

## 1. 배경 (Context)

현재 `~/.claude/projects/` 하위에 Claude Code 세션 로그가 JSONL 형태로 이미 누적되고 있다.

### 실측 현황 (2026-04-15 기준)

| 항목 | 값 |
|---|---|
| 총 세션 JSONL 파일 수 | 307개 |
| 총 용량 | 389 MB |
| 프로젝트 디렉토리 수 | 60+ (worktree 포함) |
| 엔트리 타입 | user / assistant / system / attachment / file-history-snapshot / queue-operation |
| 경로 인코딩 규칙 | `cwd`의 `/` → `-`로 치환한 디렉토리명 (예: `/Users/you/Documents/dev` → `-Users-you-Documents-dev`) |

### 스키마 (원본 JSONL 1 라인 = 1 이벤트)

```jsonc
{
  "type": "user" | "assistant" | "system" | "attachment" | "file-history-snapshot" | "queue-operation",
  "timestamp": "2026-04-07T02:08:58.610Z",
  "sessionId": "83351383-6381-42c3-b6b6-3cd393e1d043",
  "uuid": "d6e508c6-...",
  "parentUuid": "...",       // 이전 이벤트 UUID (null = 세션 시작)
  "cwd": "/Users/you",
  "gitBranch": "HEAD",
  "message": { "role": "user" | "assistant", "content": "..." | [{"type":"text","text":"..."}] }
}
```

한 세션(ef03e98c-...) 분포 예시:
- `user` 651건, `assistant` 881건, `file-history-snapshot` 153건, `system` 38건, `queue-operation` 24건, `attachment` 3건

### 문제

Raw JSONL은 이미 존재하지만 다음이 불가능하다:

1. **크로스 세션 검색** — "지난주 acme-app에서 STT 품질 관련 지시 뭐 했었지?" 같은 질문에 즉답 불가
2. **프롬프트 → 커밋 매핑** — 어떤 지시가 어떤 코드 변경을 만들었는지 추적 불가
3. **의도/결과 요약** — 세션당 수백~수천 이벤트를 매번 원본으로 읽을 수 없음
4. **프라이버시 게이트 부재** — 토큰·시크릿이 섞여 있어도 지금 구조로는 필터링 불가
5. **MEMORY.md와의 역할 혼선** — MEMORY는 "다음 세션 주입용 요약"인데, 아카이브 용도로도 쓰려는 압력이 생기면 양쪽 모두 망가진다

### 목표

- 영속적 검색·추적 가능한 세션 로그 DB 구축
- MEMORY.md(런타임 주입)와 역할 분리 유지
- 향후 CKG(Code Knowledge Graph) / LLM Wiki의 데이터 기반이 될 수 있는 포맷
- 프라이버시·보관주기를 시스템적으로 강제

### 비목표 (이번 ADR 범위 밖)

- L3 (임베딩 기반 CKG) 구현 — 데이터가 쌓이고 L2 품질 확인 후 별도 ADR
- 다른 도구(Cursor, Codex 등) 로그 통합
- 실시간 스트리밍 (배치 N분 간격이면 충분)

---

## 2. 결정 (Decision)

**4-레이어 구조**를 채택하되, 이번 ADR에서는 **L0~L2만** 확정한다.

```
L0: Raw JSONL (기존, 불변)
 └→ L1: 정규화 SQLite DB (이벤트 인덱스)
       └→ L2: 세션 요약·태깅 (의도/결정/결과)
             └→ L3: 임베딩·CKG (향후, ADR-002로 분리)
```

### 2.1 원칙

1. **L0은 건드리지 않는다** — Claude Code가 쓰는 파일을 후처리 프로세스가 수정하면 안 됨. 읽기 전용.
2. **L1은 멱등(idempotent)하게 적재** — 파일 mtime + 마지막 처리 UUID를 watermark로 관리, 몇 번을 재실행해도 중복 없음.
3. **L2는 L1에서 선별된 세션만** — 의미 있는 작업(커밋 발생, 결정 내림, 3턴 이상)만 요약 대상. 탐색·오타·취소 세션은 skip.
4. **MEMORY.md는 L2의 승격(promotion) 결과물** — 아카이브에서 찾은 "계속 유효한 피드백/사실"만 선별적으로 MEMORY로 올린다. 반대 방향(MEMORY → 아카이브) 없음.
5. **프라이버시 필터는 L1 적재 시점**에 적용 — L0은 그대로 두지만 DB에는 마스킹된 content만 들어감.

### 2.2 스토리지

| 레이어 | 포맷 | 위치 | 백업 |
|---|---|---|---|
| L0 | JSONL (기존) | `~/.claude/projects/**/*.jsonl` | Claude Code가 관리 |
| L1 | SQLite | `~/.claude-archive/sessions.db` | 주 1회 `.backup` 명령으로 `~/.claude-archive/backups/` |
| L2 | SQLite 동일 DB의 별도 테이블 | 동일 | 동일 |
| 파이프라인 코드 | Python | `~/claude-recall/tools/session-archive/` | git |

SQLite 선택 이유:
- 단일 파일, 로컬 전용, 백업 용이
- FTS5 (전문 검색) 내장 → L1에서도 content 검색 가능
- DuckDB 대비 쓰기 빈도 높은 로그 적재에 더 안정적
- 나중에 DuckDB로 읽기 전용 분석만 붙여도 됨 (`ATTACH 'sessions.db' (TYPE SQLITE)`)

### 2.3 L1 스키마

```sql
-- 세션 단위 메타
CREATE TABLE sessions (
  session_id         TEXT PRIMARY KEY,   -- Claude Code sessionId
  project_dir        TEXT NOT NULL,      -- cwd 원본 (예: /Users/you/acme-svc)
  project_slug       TEXT NOT NULL,      -- 경로 인코딩 (예: -Users-you-acme-svc)
  started_at         TEXT NOT NULL,      -- ISO8601
  ended_at           TEXT,               -- 마지막 이벤트 시각
  event_count        INTEGER NOT NULL DEFAULT 0,
  user_turn_count    INTEGER NOT NULL DEFAULT 0,
  assistant_turn_count INTEGER NOT NULL DEFAULT 0,
  git_branch         TEXT,               -- 세션 중 마지막 관측 브랜치
  source_file        TEXT NOT NULL,      -- 원본 JSONL 경로
  source_mtime       REAL NOT NULL,      -- 마지막 처리 시점 mtime (watermark)
  source_last_uuid   TEXT,               -- 마지막 처리 이벤트 UUID (재개용)
  promoted_to_l2     INTEGER NOT NULL DEFAULT 0  -- L2 요약 생성 여부
);

-- 이벤트 단위 로그
CREATE TABLE events (
  uuid          TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL REFERENCES sessions(session_id),
  parent_uuid   TEXT,
  type          TEXT NOT NULL,           -- user/assistant/system/tool_use/tool_result
  timestamp     TEXT NOT NULL,
  role          TEXT,                    -- user/assistant (메시지인 경우만)
  content       TEXT,                    -- 마스킹 적용된 텍스트 (멀티파트는 JSON 직렬화)
  content_hash  TEXT,                    -- SHA256 (원본 기준, 중복/무결성 체크)
  tool_name     TEXT,                    -- assistant tool_use인 경우
  cwd           TEXT,
  git_branch    TEXT,
  masked        INTEGER NOT NULL DEFAULT 0,  -- 1 = 마스킹 적용됨
  token_count   INTEGER                  -- 추정치 (content 기준, tiktoken 없이 len/4)
);

CREATE INDEX idx_events_session ON events(session_id, timestamp);
CREATE INDEX idx_events_type ON events(type);
CREATE INDEX idx_sessions_project ON sessions(project_slug, started_at DESC);

-- FTS (user/assistant 메시지만)
CREATE VIRTUAL TABLE events_fts USING fts5(
  uuid UNINDEXED, session_id UNINDEXED, content,
  tokenize = 'unicode61'
);

-- 파일 히스토리 스냅샷 (file-history-snapshot 타입은 별도 테이블로 분리 — 용량 큼)
CREATE TABLE file_snapshots (
  uuid         TEXT PRIMARY KEY,
  session_id   TEXT NOT NULL,
  timestamp    TEXT NOT NULL,
  file_path    TEXT NOT NULL,
  snapshot_ref TEXT                      -- 원본 파일 내 offset 또는 해시 (본문 미저장)
);
```

**의도적 제외**: `attachment` 바이너리는 저장하지 않고 메타만(`type='attachment'`로 `events`에 1 row).

### 2.4 L2 스키마

```sql
CREATE TABLE session_summaries (
  session_id     TEXT PRIMARY KEY REFERENCES sessions(session_id),
  intent         TEXT NOT NULL,        -- "acme STT OCR 통합 제거 논의" 같은 한 줄
  outcome        TEXT,                 -- "결정: OCR 유지 / 커밋 없음"
  decisions_json TEXT,                 -- [{"decision":"...","rationale":"..."}]
  tags_json      TEXT,                 -- ["acme","stt","policy"]
  related_commits_json TEXT,           -- ["sha1","sha2"]
  files_touched_json TEXT,             -- ["src/a.ts","src/b.ts"]
  model          TEXT,                 -- 요약 생성에 쓴 모델
  summary_cost_usd REAL,
  summarized_at  TEXT NOT NULL,
  quality_score  INTEGER               -- 자체 평가 0-10 (옵션)
);
```

### 2.5 L1 적재 파이프라인

```
scan() → filter() → parse() → mask() → upsert() → fts_index()
```

1. **scan**: `~/.claude/projects/**/*.jsonl` 글롭, `sessions` 테이블의 `source_mtime`과 비교해서 변경된 파일만 선별
2. **filter**: 
   - 0 byte 파일 skip
   - 테스트/hello 세션 skip (user 턴 < 2 && 총 이벤트 < 5)
3. **parse**: 라인 단위 JSON 파싱, 파싱 실패는 `parse_errors` 테이블에 기록 (DLQ)
4. **mask**: 아래 2.7 규칙 적용
5. **upsert**: `events.uuid` PK 기준 UPSERT. `sessions.source_last_uuid`를 해당 파일 마지막 성공 이벤트로 갱신
6. **fts_index**: user/assistant 텍스트 events_fts에 삽입

### 2.6 L2 요약 파이프라인

**트리거 조건** (OR):
- 세션에 `user_turn_count >= 3` AND 마지막 이벤트 이후 6시간 이상 경과 (= 종료된 세션)
- 세션 기간 동안 해당 `project_dir`에 커밋 발생 (git log로 확인)
- 사용자 수동 승격 (`session-archive promote <session_id>`)

**프롬프트 구조** (요약 생성 시):
```
[시스템] 너는 Claude Code 세션을 3-5문장으로 요약한다. 
출력 JSON: {intent, outcome, decisions[], tags[], quality_score}

[입력] 세션 이벤트 요약 (user 턴 전문 + assistant 턴 첫 200자 + tool_use 이름만)
```

**예산**:
- 세션당 입력 토큰 상한: 20K (초과 시 user 턴 우선으로 절단)
- 모델: Haiku 기본, 품질 낮으면 Sonnet으로 재시도
- 1회 예산 초과($10/일) 감지 시 일시 정지

### 2.7 프라이버시·마스킹 규칙

**마스킹 대상** (정규식 기반, L1 적재 시점에 적용):

| 종류 | 패턴 | 치환 |
|---|---|---|
| OpenAI/Anthropic key | `sk-[A-Za-z0-9_-]{20,}`, `sk-ant-[A-Za-z0-9_-]{20,}` | `[REDACTED:API_KEY]` |
| GitHub token | `ghp_[A-Za-z0-9]{36}`, `github_pat_[A-Za-z0-9_]{82}` | `[REDACTED:GH_TOKEN]` |
| AWS access key | `AKIA[0-9A-Z]{16}` | `[REDACTED:AWS_KEY]` |
| JWT | `eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+` | `[REDACTED:JWT]` |
| 이메일 | `[\w.+-]+@[\w-]+\.[\w.-]+` | `[REDACTED:EMAIL]` (옵션, 기본 OFF — 본인 이메일만 예외 가능) |
| 환경변수 라인 | `(?i)(password\|secret\|api[_-]?key\|token)\s*[:=]\s*\S+` | 키는 유지, 값만 `[REDACTED]` |
| 파일 경로 중 홈 | `/Users/you/` | 유지 (로컬 전용이므로 PII로 간주 안 함) |

**마스킹 원칙**:
- 마스킹 발생 시 `events.masked = 1` 플래그, 개수는 `mask_stats` 테이블에 집계
- 원본은 L0 JSONL에 그대로 있으므로 필요 시 되돌아볼 수 있음 (감사 로그 용도)
- 마스킹 실패는 silent pass 금지 — 검증 테스트로 보장

### 2.8 보관 주기 (Retention)

| 레이어 | 보관 | 정리 방법 |
|---|---|---|
| L0 | 무제한 (Claude Code가 관리) | 사용자가 수동 정리 |
| L1 events | 180일 기본, "승격됨"(L2 있음) 세션은 무제한 | 야간 배치로 `DELETE FROM events WHERE session_id IN (...)` |
| L1 sessions 메타 | 무제한 | - |
| L2 summaries | 무제한 | - |
| file_snapshots | 30일 | - |
| 백업 | 주 1회, 4주분 보관 | rotate |

**근거**: 원본(L0)이 남아있으므로 L1은 언제든 재생성 가능. 용량은 L1이 제일 크므로 여기서 자름.

### 2.9 CLI 인터페이스 (L1+L2 완료 후)

```
session-archive ingest              # 변경된 JSONL을 L1에 적재 (기본: 증분)
session-archive ingest --full       # 전체 재적재
session-archive summarize           # L2 요약 생성 (트리거 조건 만족한 세션)
session-archive summarize <session_id>  # 수동 승격
session-archive search "STT OCR"    # FTS 검색 (L1)
session-archive search "STT" --project acme-app --since 7d
session-archive show <session_id>   # 세션 전체 (요약 + 이벤트 타임라인)
session-archive stats               # 프로젝트별 세션 수, 용량, 최근 활동
session-archive backup              # SQLite backup
session-archive gc                  # 보관 주기 정리
```

### 2.10 MEMORY.md와의 경계

| 항목 | MEMORY.md | L1/L2 아카이브 |
|---|---|---|
| 용도 | 세션 시작 시 컨텍스트 주입 | 사후 검색·추적 |
| 크기 | 작음 (200줄 제한) | 수 GB 가능 |
| 업데이트 | Claude가 실시간 | 배치 (ingest) |
| 선별 기준 | "다음 세션에도 유효한 규칙/사실" | "일어난 모든 일" |
| 접근 | 프롬프트에 직접 포함 | CLI/MCP 검색 |

**승격 흐름** (L2 → MEMORY.md):
1. L2 요약에서 "반복되는 피드백", "명시적 결정", "계속 유효한 사실" 감지
2. 사용자에게 제안 (자동 작성 금지)
3. 승인 후 MEMORY.md에 한 줄 추가

**역방향 금지**: MEMORY.md 내용을 L1/L2로 복사하지 않음. L0→L1→L2→(승격)→MEMORY의 단방향.

---

## 3. 대안 검토 (Alternatives Considered)

### A. DuckDB 단일 레이어

- 장점: 분석 쿼리 빠름, Parquet 출력 용이
- 단점: 쓰기 트랜잭션 빈도 높을 때 SQLite보다 불리, FTS 확장성 약함
- **각하**: L1은 쓰기 빈도가 주 패턴. L3(분석)에서 DuckDB 부착하는 편이 나음

### B. 로그 파이프라인을 Hook으로 실시간 적재

- `settings.json`의 Stop/UserPromptSubmit 훅에서 바로 DB write
- 장점: 지연 없음
- 단점: Claude Code 세션 성능에 영향, 훅 실패 시 데이터 유실, 멱등성 설계 복잡
- **각하**: 배치로 충분. 훅은 트리거만 (`ingest` 호출) 가능성 있음, 구현은 L1 완성 후 판단

### C. 원본 JSONL을 그대로 grep하는 CLI만 만들기

- 장점: DB 불필요, 구현 빠름
- 단점: 크로스 세션 조인 불가, 마스킹 불가, 크기 증가 시 느려짐
- **각하**: 본 목적(CKG 기반)과 맞지 않음

### D. 클라우드 DB (Postgres, Supabase)

- 장점: 어디서든 접근, 팀 공유
- 단점: 로컬 프롬프트 유출 위험, 인프라 오버헤드, 혼자 쓰는 시스템
- **각하**: 로컬 전용 유지. 팀 공유 필요 시 별도 ADR

---

## 4. 결과·영향 (Consequences)

### 긍정
- 크로스 세션 검색 가능 → "지난주 acme-app에 뭐 지시했지" 즉답
- 프롬프트↔커밋 매핑 → 의사결정 근거 추적
- MEMORY.md 오염 방지 (명확한 역할 분리)
- L3(CKG) 진입 시 깨끗한 데이터 기반

### 부정/리스크
- **디스크**: L1이 원본의 1.5~2배가 될 수 있음 (FTS 인덱스). 180일 정리로 상쇄
- **요약 품질**: L2 결과가 나쁘면 쓰레기 누적 → `quality_score` 낮은 것은 재요약 대상
- **마스킹 누락**: 패턴 기반이라 완벽하지 않음 → 정기 샘플링 감사 필요
- **유지보수**: 파이프라인 자체가 또 하나의 시스템 → `tools/session-archive/` 폴더에 README + 테스트 필수

### 성공 기준 (L1+L2 완료 시점 검증)
1. `session-archive ingest`가 307개 기존 파일을 10분 내에 처리
2. `session-archive search "STT"`가 1초 내 응답
3. L2 요약 샘플 20개 중 15개 이상이 "읽을 만한" 품질 (사용자 육안 평가)
4. 마스킹 테스트 100% 통과 (fixture 기반)
5. 파이프라인 자체 테스트 커버리지 80% (testing-standards.md 기준)

---

## 5. 구현 로드맵

### Phase 0: ADR 승인 (현재)
- [x] ADR-001 초안 작성
- [ ] 사용자 리뷰·승인 → 상태 `Accepted`

### Phase 1: L1 (L2 설계는 고정해두고 구현은 뒤로)
1. `tools/session-archive/` 스켈레톤 + `pyproject.toml`
2. 스키마 마이그레이션 (`schema.sql`)
3. `ingest` 커맨드 (scan → parse → mask → upsert)
4. 마스킹 규칙 유닛 테스트 (fixture 20개 이상)
5. FTS 인덱스 + `search` 커맨드
6. `show`, `stats`, `backup`, `gc`
7. 기존 307 파일 전체 적재 + 검증

### Phase 2: L2
1. 트리거 조건 판별 (커밋 매칭 포함)
2. 요약 프롬프트 설계 + 골든셋 10개
3. Haiku 기반 요약 생성 + 예산 가드
4. 품질 평가 루프
5. MEMORY 승격 제안 CLI

### Phase 3: 운영화
1. launchd/cron으로 야간 `ingest` + 주 1회 `backup` + 일 1회 `summarize`
2. MCP 서버로 노출 (다른 Claude 세션에서 조회 가능)
3. 필요 시 Stop 훅에서 `ingest` 자동 호출

---

## 6. 결정된 사항 (Resolved 2026-04-15)

1. **파이프라인 코드 위치**: `claude-recall/tools/session-archive/` 하위 (확정)
2. **MCP 노출 범위**: 검색(read) + 승격(write) 둘 다 (확정). 승격은 별도 write tool로 분리해 실수 방지
3. **이메일 마스킹 기본값**: OFF (확정). 본인 환경 전용이므로 노이즈만 늘어남
4. **L3 CKG 데이터 모델**: 이번 ADR 범위 밖 → 별도 ADR-002에서 다룸

---

## 7. 참고

- `/Users/you/claude-recall/docs/documentation-rules.md`
- `/Users/you/claude-recall/docs/testing-standards.md`
- `/Users/you/claude-recall/docs/error-handling.md`
- Claude Code 세션 파일 실측: `~/.claude/projects/**/*.jsonl` (307 files / 389 MB / 2026-04-15)
