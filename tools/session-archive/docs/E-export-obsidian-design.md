# E 단계: Obsidian Vault Export 설계 노트

> ADR-001 Phase 4 (L2 → 마크다운 vault). 정식 ADR 없이 design notes로 잠금. 구현 후 Phase 1 검증 통과 시 ADR-001 §5에 1줄 추가.
>
> 작성일: 2026-04-27
> 결정 입력: `project_session_archive_e_plan.md`, `project_oss_llm_archive_integration.md`, `project_oss_llm.md`

---

## 1. 책임 분리 (단일 책임 원칙)

| 영역 | 담당 |
|---|---|
| L1·L2 ingestion | session-log-archive |
| Vault export (`~/llm-wiki/`) | session-log-archive (E) |
| Vault 자동 갱신 (cron/launchd) | session-log-archive (C) |
| MCP write tool | session-log-archive (D) |
| Vault 임베딩 + RAG/LoRA + LLM 추론 | oss-llm |
| **메모리 시스템 (`~/.claude/projects/-Users-cjons/memory/`) 인덱싱** | **oss-llm (별도 LanceDB 테이블)** |

E 단계는 **마크다운 vault 생성·갱신까지만** 책임. 임베딩·RAG는 oss-llm `~/oss-llm-poc/scripts/rag_ingest.py`가 vault 경로 인자로 받아서 별도 처리.

### vault 정의 (2026-04-27 명문화)

**vault = `~/llm-wiki/` 디렉터리 + session-archive export 산출물 단독**.
- `sessions/` (E v1)
- `decisions/` (E v1.5)
- `projects/` (v1.5 2순위 미정)
- `MEMORY/` 심볼릭 링크 — **거부 (2026-04-27 결정)**

**Why MEMORY/ symlink 거부**:
- 메모리 시스템(`~/.claude/projects/-Users-cjons/memory/`)은 Claude Code 자동 로드 영역 + 사용자 직접 편집 영역. session-archive가 관리하지 않음
- vault에 symlink 추가 시 vault 정의가 "export 산출물 + memory"로 확장 → 책임 경계 흐림
- 단일 LanceDB 테이블에 섞이면 가중치·필터 분리 어려움
- vault git repo에 broken symlink 가능성

**대신**: oss-llm 측이 `rag_ingest.py ~/.claude/projects/-Users-cjons/memory --table memory`로 별도 LanceDB 테이블 인덱싱.

**검증 결과 (2026-04-28, oss-llm P0 v4)**: vault 7,841 + memory 669 dual-table 운영. P0 v4 5/5 통과. 두 테이블 거리 분포 다름 → **union 금지**, 분리 top-k + 출처 마커(⭐M / V) 패턴 정립 (`reference_dual_table_rag_pattern.md` 메모리 참조). Phase 1 종결.

## 2. Vault 구조

```
~/llm-wiki/
├── sessions/YYYY-MM-DD-<projslug>__<sid8>.md   ← v1 핵심 산출물
├── projects/<slug>.md                           ← v2 (수동/반자동)
├── decisions/D-YYYYMMDD-<slug>.md               ← v2
├── files/                                       ← v2 (많이 건드린 파일만)
└── MEMORY/                                      ← 현 memory 시스템 심볼릭 링크 (v2)
```

**v1 범위**: `sessions/` 디렉터리만 자동 생성. `projects/`, `decisions/`, `files/`, `MEMORY/`는 v2.

## 3. 파일명 규칙 (rename 금지 — oss-llm 인덱싱 캐시 안정)

```
sessions/YYYY-MM-DD-<projslug>__<sid8>.md
```

- `YYYY-MM-DD`: `started_at` UTC 기준
- `<projslug>`: `sessions.project_slug` (이미 경로 인코딩됨, `/` → `-`)
- `__<sid8>`: `session_id`의 **SHA-1 hash 앞 8자**
- **확정 후 절대 변경 금지** — oss-llm `rag_ingest.py`의 mtime 증분 인덱싱이 파일 경로를 캐시 키로 사용. rename 시 인덱싱 처음부터.

> ⚠️ **단순 prefix 금지** — 초기 설계는 session_id 앞 8자였으나 합성키
> `{parent}::sub::{agent_stem}`이 parent와 같은 prefix를 가져 295건 → 76 파일
> (74% 손실) 실측 발생. SHA-1 hash 8자로 회피 (충돌 확률 ~3e-6 at 295건).

같은 날·같은 프로젝트 다중 세션 충돌은 hash 차이로 자연 해결.

## 4. Frontmatter 표준 (11 필드)

```yaml
---
session_id: 83351383-6381-42c3-b6b6-3cd393e1d043
project: -Users-cjons-tms-stt
branch: dev
summarized_at: 2026-04-25T14:30:00Z
model: claude-haiku-4-5-20251001
quality_score: 8
summary_level: L2
kind: session
lang: ko
tags: [tms-stt, stt, policy]
files_touched: [src/a.ts, src/b.ts]
---
```

| 필드 | 출처 | 용도 |
|---|---|---|
| `session_id` | sessions.session_id (full uuid) | RAG 인용 정확성, MCP get_session 키 |
| `project` | sessions.project_slug | 프로젝트별 필터·집계 |
| `branch` | sessions.git_branch | 워크트리/브랜치별 분석 |
| `summarized_at` | session_summaries.summarized_at | **증분 export 기준 (핵심)** |
| `model` | session_summaries.model | 모델별 품질 추적 |
| `quality_score` | session_summaries.quality_score | RAG 검색 가중치 |
| `summary_level` | 고정 `L2` (v1) | oss-llm 권장 — 향후 L1 export 도입 시 분기 |
| `kind` | 고정 `session` (v1) | oss-llm 권장 — projects/decisions 도입 시 분기 |
| `lang` | intent/outcome 자동 감지 | oss-llm 권장 — 다국어 임베딩 분기 |
| `tags` | session_summaries.tags_json | Obsidian + RAG 양쪽 활용 |
| `files_touched` | session_summaries.files_touched_json | RAG 코드 검색 + Obsidian backlink |

**`lang` 자동 감지**: intent + outcome 텍스트의 한글/한자/히라가나/카타카나/라틴 비율로 결정. 한글 ≥30% → `ko`, 히라가나·카타카나 ≥10% → `ja`, 한자(CJK Unified) ≥30% & 한글<10% → `zh`, 그 외 → `en`. 5문자 미만이면 `und`(unknown).

**비용 메타 (`cost_usd`/`input_tokens`/`output_tokens`) 제외**: 운영 분석은 DB 직접 쿼리. vault에 두면 RAG 청크 노이즈만 늘어남.

## 5. 본문 헤더 표준 (RAG 청크 친화 7섹션)

```markdown
# {intent}

## Intent
{outcome 한 줄 + intent 부연}

## Outcome
{decisions/포기/커밋 결과}

## Decisions
1. **{decision[0].decision}**
   - 근거: {decision[0].rationale}
2. ...

## Files Touched
- src/a.ts
- src/b.ts

## Related Commits
- {sha[0]} {commit message 첫 60자}
- ...

## User 원문 (token 기준 상위 3턴)
> [!quote] 1
> {user content, 500자 절단}

## Backlinks
- Project: [[projects/-Users-cjons-tms-stt]]
- Files: [[files/src-a]] [[files/src-b]]
```

**순서 확정 근거**: oss-llm `rag_ingest.py`가 헤더 단위 분할 + 슬라이딩 윈도우(800자/100자 overlap). RAG 청크 우선순위가 높은 항목(Intent/Outcome/Decisions)을 위로, 노이즈 가능성이 큰 User 원문·Backlinks는 아래로.

**Backlinks 분리 이유**: Obsidian wikilink는 RAG 청크엔 노이즈 (`[[files/src-a]]` 같은 토큰). 본문 내 plain 경로는 `Files Touched` 섹션, wikilink는 `Backlinks`로 분리.

## 6. User 원문 추출 (3턴 × 500자)

```sql
SELECT content, token_count FROM events
WHERE session_id = ? AND role = 'user'
ORDER BY token_count DESC, timestamp ASC
LIMIT 3
```

- `token_count` 기준 상위 3개 (DESC)
- 동률 시 `timestamp ASC` (먼저 발화한 것 우선)
- 각 500자 절단, 절단 시 `…` 추가
- `> [!quote] N` Obsidian callout 사용

## 7. 코드 블록 보존 정책 (oss-llm 권장 5번)

L1 prompt 빌더는 assistant 본문을 200자 → 100자로 절단하지만, **E 단계 user 원문 추출 시 코드 블록은 별도 보존**:

- user content 안에 ` ``` ` 펜스가 있으면 펜스 단위로 보존 (펜스 내부 길이 무관)
- 펜스 외 일반 텍스트는 500자 절단 적용
- 코드 검색 재현성 확보 (RAG 코드 청크 품질)

다만 v1에서는 **단순 구현**: 500자 절단을 펜스 인식 없이 적용. 코드 블록이 끊기는 경우 v2에서 보강.

## 8. 증분 export

```python
# 단순 정책 (v1)
last_export_at = read_state(".llm-wiki-watermark")  # 또는 빈 파일이면 epoch 0
new_summaries = SELECT * FROM session_summaries WHERE summarized_at > last_export_at
for s in new_summaries: write_md(s)
write_state(now())
```

- 기준: `session_summaries.summarized_at` 단독
- vault 파일 mtime 비교는 v2 (사용자 수동 편집 보호용)
- watermark 파일: `~/llm-wiki/.session-archive-state.json`

**v1 정책: 사용자 수동 편집 보호 OFF**. vault 전체를 export 산출물로 간주. 사용자가 노트에 추가 정보를 기록하고 싶으면 별도 영역(예: `MEMORY/`)이나 frontmatter 외 추가 섹션을 v2에서 정의.

## 9. wikilink 변환 (v1 보수적)

`Backlinks` 섹션에만 wikilink 사용:

```python
# project: -Users-cjons-tms-stt → [[projects/-Users-cjons-tms-stt]]
# file: src/a.ts                → [[files/src-a]]   (slash → dash, 확장자 제거)
```

- 한글·공백·특수문자 포함 경로: 알파벳/숫자/한글/하이픈만 유지, 그 외 `_` 치환
- v1은 backlinks만 출력. `projects/`, `files/` 노트 자체는 미생성 (Obsidian이 unresolved link로 표시 — 정상)
- v2에서 `projects/`, `files/` 노트 자동 생성 시 unresolved 해소

## 10. 태그 정규화

`tags_json` → frontmatter `tags`:

- 소문자
- 공백 → `-`
- 한글 그대로 유지 (Obsidian 한글 태그 지원)
- 영숫자·하이픈·한글 외 문자 제거
- 길이 1자 미만 또는 중복 제거

본문에는 태그 표기 없음 (frontmatter만). Obsidian 태그 검색은 frontmatter `tags`로 자동 인식.

## 11. CLI 인터페이스

```
session-archive export-obsidian [--vault PATH] [--since SUMMARIZED_AT] [--full] [--dry-run]
```

| 옵션 | 기본값 | 동작 |
|---|---|---|
| `--vault` | `~/llm-wiki` | 출력 디렉터리 |
| `--since` | watermark 파일 | 이 시점 이후 `summarized_at`만 export |
| `--full` | False | watermark 무시, 전체 재export (덮어쓰기) |
| `--dry-run` | False | 파일 작성 없이 대상 목록만 출력 |

진행 출력: 모델별 카운트 + 신규/갱신/skip 통계 + lang 분포.

## 12. 구현 순서

1. **frontmatter 빌더** (`exporter/frontmatter.py`) + 단위 테스트 (lang 감지 fixture 5종 포함)
2. **본문 빌더** (`exporter/body.py`) — Decisions/Files/Commits/User 원문/Backlinks 섹션별 함수 분리
3. **markdown writer** (`exporter/writer.py`) — frontmatter + 본문 결합, atomic write (tmp → rename)
4. **watermark store** (`exporter/state.py`) — JSON 파일 read/write
5. **export 오케스트레이터** (`exporter/__init__.py`, `export_all`)
6. **CLI 서브커맨드** (`cli.py`에 `export-obsidian` 추가)
7. **End-to-end 테스트**: 골든셋 5세션 → vault → 시각 확인 + frontmatter parse 검증

각 단계 = 1 커밋. 단위 테스트는 stdlib `unittest`로 진행 (현재 패키지 관례).

## 13. 테스트 전략

- **Unit**: lang 감지, slug 변환, wikilink 변환, 절단 로직, frontmatter 직렬화
- **Integration**: 실제 sessions.db에서 골든셋 5세션 export → 파일 존재 + frontmatter parse + 헤더 7섹션 존재 검증
- **Manual review**: 5세션 결과를 Obsidian으로 열어 시각 확인 (Graph View backlink 동작, frontmatter 태그 인식)
- **RAG 호환**: oss-llm `rag_ingest.py`가 vault 인덱싱 정상 처리하는지 cross-check (oss-llm 세션에서)

## 14. 성공 기준

1. `session-archive export-obsidian --full`이 295건을 5분 내 처리
2. 5세션 시각 검수 통과 (Obsidian 가독성, RAG 청크 품질)
3. lang 자동 감지 정확도 ≥95% (한국어 코퍼스 기준)
4. 증분 모드: 신규 세션만 처리 — 기존 파일은 mtime 변경 없음
5. oss-llm `rag_ingest.py ~/llm-wiki/`가 에러 없이 LanceDB 인덱싱 완료

## 15. 비범위 (v1 제외) → v1.5 / v2 / 후속 분리

### v1.5 (다음 진입, 2026-04-27 결정)
- **`decisions/` 디렉터리 자동 생성** ⭐ 1순위 (oss-llm Phase 1 P0 v2 피드백)
- **`projects/` 디렉터리** ⭐ 2순위 (수동/반자동, 메모리 연계)
- **`MEMORY/` 심볼릭 링크** ⭐ 3순위 (`~/.claude/projects/-Users-cjons/memory/` → `~/llm-wiki/MEMORY/`)
- (선택) frontmatter `related_decisions`, `related_keywords` metadata 보강

### v2
- 사용자 수동 편집 보호 (mtime 비교, conflict 감지)
- 코드 블록 펜스 인식 절단
- Related Commits 메시지 60자 추가
- L1 export (`summary_level: L1`)
- `files/` 디렉터리 (많이 건드린 파일만)

### 후속 단계
- 자동 cron/launchd 등록 (C 단계)
- MCP read/write tool (D 단계)

## 16. v1.5 — decisions/ export (2026-04-27 진입)

### 데이터 소스
- 1차: `session_summaries.decisions_json` (이미 채워짐, 295 세션 × 평균 1.4 = 약 412 결정)
- 2차 (선택, v1.6+): 메모리 카테고리 의사결정 추출

### 파일명 규칙
```
decisions/D-YYYYMMDD-<decision_slug>__<hash8>.md
```
- `YYYYMMDD`: 원본 세션 `summarized_at` UTC 기준
- `decision_slug`: decision 텍스트 첫 30자, 한글·영숫자·하이픈만, slug화
- `hash8`: SHA-1(`session_id` + `decision_index`) 앞 8자 (멱등성 보장)

### Frontmatter (8필드)
```yaml
---
session_id: <원본 세션 uuid>
decision_index: 0
project: <session.project_slug>
summarized_at: <원본 세션 ts>
kind: decision
lang: ko|en
tags: <원본 세션 tags 그대로>
quality_score: <원본 세션 점수>
---
```

### 본문
```markdown
# {decision}

## 결정
{decision}

## 근거
{rationale}

## 컨텍스트
- 원본 세션: [[sessions/YYYY-MM-DD-projslug__sid8]]
- 프로젝트: [[projects/<slug>]]
- 결정 시점: {summarized_at}

## Tags
{tags 줄 (소문자·하이픈)}
```

### CLI 옵션
```
session-archive export-obsidian [--vault] [--since] [--full] [--dry-run]
                                [--kinds sessions,decisions,projects]
```
- 기본값: `sessions,decisions` (v1.5)
- v1 호환 모드: `--kinds sessions`

### 증분 정책
- decisions/는 sessions/와 같은 watermark 사용 (`summarized_at` 기준)
- 같은 세션의 decisions가 변경되면 모두 재생성 (덮어쓰기)

---

## 부록 A: oss-llm 연동 (Phase 1)

E 단계 완료 후 oss-llm 측 작업:

```bash
source ~/oss-llm-poc/.venv/bin/activate
python ~/oss-llm-poc/scripts/rag_ingest.py ~/llm-wiki/
# → ~/oss-llm-poc/data/lancedb/vault 테이블 생성

python ~/oss-llm-poc/scripts/rag_ask.py "tms-stt OCR 정책 결정 이유는?" --model qwen2.5-14b-instruct --k 5
```

`rag_ingest.py`의 vault 경로만 변경. session-log-archive는 vault 생성·갱신까지, oss-llm은 vault 소비. 양쪽 코드 결합도 0.
