# L2 요약 파이프라인 설계 노트 (ADR-001 Phase 2 예정)

> 이 문서는 구현 전 설계 초안. Phase 1 검증 후 ADR 업데이트와 함께 확정.

## 트리거 조건

세션이 L2 요약 대상이 되는 조건 (OR):

1. `user_turn_count >= 3` AND `datetime.now() - max(ended_at) >= 6h`
   — 종료된 의미 있는 세션
2. 세션 기간(started_at~ended_at) 동안 해당 `project_dir`에 git 커밋 발생
   — `git log --since --until --all` 로 확인, 매칭되는 커밋 SHA 수집
3. 사용자 수동 승격: `session-archive promote <session_id>`

## 입력 구성 (프롬프트 컨텍스트)

```
[시스템 메시지]
당신은 Claude Code 세션 로그를 3~5문장으로 요약한다.
출력 스키마: JSON {intent, outcome, decisions[], tags[], files_touched[], quality_score(0-10)}
- intent: 사용자가 하려고 한 것 (한 줄)
- outcome: 실제 결과 (커밋/결정/포기)
- decisions: [{decision, rationale}, ...] 최대 5개
- tags: 프로젝트·기술·주제 키워드 (예: ["tms-stt", "STT", "policy"])
- files_touched: 이 세션에서 편집/생성된 파일 경로
- quality_score: 요약의 확신도 (0=모름, 10=명확)

[세션 메타]
session_id: <>
project: <project_dir>
branch: <git_branch>
started_at ~ ended_at
related_commits: [sha1, sha2, ...]
event_counts: user=N assistant=M tool_calls=K

[이벤트 시퀀스]
- 모든 user 턴: 전문
- assistant 턴: 첫 200자 + tool_use 이름 리스트 (본문 제외)
- tool_result: 스킵
- file-history-snapshot: 대표 파일 경로만
```

## 토큰 예산

- 세션당 입력 상한: **20K 토큰**
- 초과 시 절단 정책:
  1. tool_result 제외 (이미 기본)
  2. assistant 본문 100자로 더 절단
  3. 오래된 user 턴부터 제외 (최근 우선)
  4. 그래도 초과 시: 앞뒤 10턴씩만 샘플링 + "[... middle omitted ...]"
- 일일 예산: **$10** — 초과 감지 시 `summarize` 일시 정지

## 모델 선택

- **기본**: `claude-haiku-4-5-20251001`
- **재시도 조건**: `quality_score < 5` OR JSON 파싱 실패
- **재시도 모델**: `claude-sonnet-4-6`
- **재시도 상한**: 세션당 2회

## 실패 처리

- JSON 파싱 실패 → Sonnet 재시도
- 2회 실패 → `session_summaries.summarized_at` NULL 유지 + 에러 테이블 기록
- API 에러 (5xx, timeout) → 지수 백오프 1s→2s→4s, 3회 후 포기

## 커밋 매칭 (trigger 조건 2번)

```python
def find_related_commits(project_dir: str, started_at: str, ended_at: str) -> list[str]:
    # git -C project_dir log --since=started_at --until=ended_at+2h --format=%H --all
    # 확장 윈도우 +2h는 세션 종료 직후 commit push 케이스 커버
```

- 윈도우 확장: `ended_at + 2h`
- `project_dir`에 .git이 없으면 skip (`(unknown)` 같은 케이스)
- 브랜치 제한 없음 (`--all`)

## 품질 평가 (자체)

`quality_score` 낮으면 Sonnet 재시도. 사용자 육안 평가용으로 CLI에 노출:

```
session-archive summarize --re-eval     # quality_score < 5 인 요약만 Sonnet로 재시도
session-archive summarize --sample 20   # 최근 20개 랜덤 샘플 출력
```

## MEMORY 승격 (수동)

L2 요약을 훑어 "계속 유효한 피드백·사실"을 MEMORY.md로 수동 승격:

```
session-archive memory-suggest   # 최근 요약에서 승격 후보 제안
session-archive memory-promote <session_id> --type feedback
```

MCP write tool로도 노출 (ADR §2.9 결정사항).

## 스키마 (이미 ADR-001에 확정, 재기록)

```sql
CREATE TABLE session_summaries (
  session_id           TEXT PRIMARY KEY REFERENCES sessions(session_id),
  intent               TEXT NOT NULL,
  outcome              TEXT,
  decisions_json       TEXT,
  tags_json            TEXT,
  related_commits_json TEXT,
  files_touched_json   TEXT,
  model                TEXT,
  summary_cost_usd     REAL,
  summarized_at        TEXT NOT NULL,
  quality_score        INTEGER
);
```

## 구현 순서

1. commit-matcher (subprocess git log)
2. trigger-filter (trigger 조건 판별)
3. prompt-builder (이벤트 → 컨텍스트 문자열, 예산 가드)
4. anthropic-client (재시도 + 예산 트래킹)
5. summarizer (prompt + client → row)
6. `session-archive summarize` CLI
7. 골든셋 10개로 품질 확인 → 튜닝
