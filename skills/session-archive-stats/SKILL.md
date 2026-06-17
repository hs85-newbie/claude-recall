---
name: session-archive-stats
description: |
  session-archive 아카이브 현황을 출력한다: 총 세션/이벤트 수, 프로젝트별 활동, 마스킹 통계, 파싱 에러.
  사용자가 "세션 현황", "아카이브 상태", "stats", "session-archive 상태", "얼마나 쌓였어"라고 할 때 호출.
  /session-archive-ingest 직후 자연스럽게 제안.
allowed-tools:
  - Bash
---

# session-archive-stats

`~/.claude-archive/sessions.db`의 적재 현황을 요약 출력한다.

## 실행

```bash
cd ~/my-claude-global/tools/session-archive && .venv/bin/session-archive stats
```

## 채팅 보고 형식

출력에서 다음을 추출해 간결히 정리:

```
{총_세션} 세션 · {총_이벤트} 이벤트 · DB {크기}MB · 마지막 활동 {last}
상위 프로젝트: 1) {proj1}({n1}) 2) {proj2}({n2}) 3) {proj3}({n3})
마스킹: env_var {n}건 외 {m}건
```

- `parse_errors > 0` 이면 강조 후 "parse_errors 테이블 확인 필요" 안내
- `promoted_to_l2` 비율이 0 근처면 "L2 요약 미착수 상태" 덧붙임

상세 md 저장 금지 — 단발성 조회라 채팅 요약만.

## 추가 질의 예시 (사용자가 이어서 물을 수 있음)

| 질문 | 명령 |
|---|---|
| "acme 프로젝트만 보고 싶어" | `session-archive search "<keyword>" --project acme` |
| "세션 하나 자세히" | `session-archive show <session_id> --timeline` |
| "백업 떠줘" | `session-archive backup` |

이 경우에도 stats 출력 자체를 md로 저장하지는 않음 — 단발 조회.
