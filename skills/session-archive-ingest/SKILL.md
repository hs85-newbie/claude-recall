---
name: session-archive-ingest
description: |
  Claude Code 세션 JSONL을 ~/.claude-archive/sessions.db에 증분 적재한다.
  사용자가 "세션 적재", "세션 아카이브", "ingest", "session-archive", "세션 로그 저장"이라고 할 때 호출.
  세션 종료 시점이나 새 세션 시작 직후 proactive하게 제안해도 좋다.
  ADR-001 Phase 1 L1 파이프라인의 진입점.
allowed-tools:
  - Bash
---

# session-archive-ingest

`~/.claude/projects/**/*.jsonl` 중 mtime이 변경된 파일만 `~/.claude-archive/sessions.db`에 증분 적재한다.

- parent / subagent 분리 (`{parent_sid}::sub::{agent_stem}` 합성키)
- 시크릿 마스킹 (env_var / sk-* / ghp_* / AKIA* / JWT)
- FTS5 전문 검색 인덱스 자동 갱신
- 증분 소요: 일반적으로 3분 내외

## 실행

```bash
cd ~/my-claude-global/tools/session-archive && .venv/bin/session-archive ingest
```

## 결과 요약 (채팅 보고 형식)

출력에서 다음 값을 추출해 한 줄로 보고:

```
[ingest] {elapsed}s · 신규/변경 {processed} 세션 · {events_upserted} 이벤트 · skip {skipped_unchanged} · 마스킹 {mask_hits_total} (errors={parse_errors})
```

- `parse_errors > 0` 이면 ⚠️ 강조 + `parse_errors` 테이블 확인 안내
- `processed=0 skipped_unchanged=307` 식이면 "변경 없음" 단 한 줄로

상세 md 저장 금지 — 단발성 배치 작업이라 채팅 요약만.

## 실패 처리

| 증상 | 조치 |
|---|---|
| `no such column` / FTS 에러 | 쿼리 sanitize 문제. CLI 코드 확인 |
| `IntegrityError: NOT NULL` | 세션 메타 추출 실패. 원본 JSONL 구조 변경 가능성 — `src/session_archive/ingest.py` 재검토 |
| venv 손상 / `No module named` | `cd ~/my-claude-global/tools/session-archive && rm -rf .venv && python3 -m venv .venv && .venv/bin/pip install -e .` |
| 첫 적재(21분+) | 정상. 다음부터는 mtime 증분. |

## 다음 액션 제안 (선택)

적재 완료 후 사용자가 원한다면:
- 현재 상태 확인 → `/session-archive-stats`
- 특정 키워드 검색 → `session-archive search "<query>"`
- 특정 세션 상세 → `session-archive show <session_id> --timeline`
