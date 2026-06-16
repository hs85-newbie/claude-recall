#!/usr/bin/env bash
# ~/.claude/hooks/route-to-local.sh
# UserPromptSubmit 훅 — 로컬 LLM으로 위임 가능한 작업 감지 시 힌트 출력
# 입력: stdin으로 JSON (prompt 필드)
# 출력: stdout (Claude에 추가 컨텍스트로 전달됨)
set -euo pipefail

INPUT=$(cat)
PROMPT=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('prompt', ''), end='')
except Exception:
    pass
")

MATCHERS=("요약해" "번역해" "포맷팅" "포매팅" "정렬해" "변환해" "추출해")

for keyword in "${MATCHERS[@]}"; do
  if [[ "$PROMPT" == *"$keyword"* ]]; then
    if curl -sf http://localhost:1234/v1/models > /dev/null 2>&1; then
      STATUS="활성"
    else
      STATUS="비활성 — 클라우드 폴백"
    fi
    cat <<HINT
[라우팅 힌트] 기계적 변환 작업으로 판단됩니다.
→ local-ops 에이전트(Gemma-4-26B, 로컬 무료) 사용을 권장합니다.
→ LM Studio 서버: $STATUS
HINT
    exit 0
  fi
done
exit 0
