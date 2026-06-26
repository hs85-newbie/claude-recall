#!/usr/bin/env bash
# git-safety-guard.sh — PreToolUse(Bash) 훅
#
# WHY: AI 에이전트가 일으킨 가장 위험한 사고가 git 파괴적 연산이었다.
#      `git reset --hard`가 로컬 커밋을 날렸고, origin 덕에 겨우 복구된 사례가 있다.
#      이 훅은 되돌리기 어려운 git 명령을 실행 직전 가로채 사용자 확인을 요구한다.
#
# 동작: stdin으로 받은 Bash 명령에서 위험 패턴을 찾으면 permissionDecision=ask 를
#       출력해 사용자 확인을 띄운다. 그 외엔 조용히 통과(allow).
# 철칙: fail-open — 파싱/판정 중 어떤 오류가 나도 작업을 막지 않는다(exit 0).
#       가드가 정상 작업을 막는 것이 사고보다 더 큰 마찰이기 때문.
set -uo pipefail

ALLOW() { exit 0; }   # 통과(아무 출력 없으면 기본 권한 흐름)

ASK() {
  # $1 = 사용자에게 보일 사유
  python3 - "$1" <<'PY' 2>/dev/null || ALLOW
import json, sys
reason = sys.argv[1]
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": reason,
    }
}, ensure_ascii=False))
PY
  exit 0
}

INPUT=$(cat 2>/dev/null) || ALLOW
[ -n "$INPUT" ] || ALLOW

# tool_input.command 추출 (Bash 도구). 파싱 실패 시 통과.
CMD=$(printf '%s' "$INPUT" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    ti = d.get("tool_input") or {}
    print(ti.get("command", ""))
except Exception:
    print("")
' 2>/dev/null) || ALLOW

[ -n "$CMD" ] || ALLOW
case "$CMD" in *git*) ;; *) ALLOW ;; esac   # git 명령 아니면 즉시 통과

# 위험 패턴 판정 (grep -E, 대소문자 무시). 매칭되는 첫 사유로 ask.
match() { printf '%s' "$CMD" | grep -Eiq -e "$1"; }

if match 'git[[:space:]]+([^&|;]*[[:space:]])?reset[[:space:]]+([^&|;]*[[:space:]])?--hard'; then
  ASK "⚠️ git reset --hard 감지 — 현재 브랜치의 커밋·작업이 영구 소실될 수 있습니다. 진행 전 백업 브랜치(git branch backup-$(date +%s))를 만들었는지 확인하세요. 정말 실행할까요?"
fi

# force push: --force 또는 -f. 단 --force-with-lease(안전한 형태)는 제외.
if match 'git[[:space:]]+([^&|;]*[[:space:]])?push' && match '(--force([^-]|$)|[[:space:]]-f([[:space:]]|$))' && ! match '\-\-force-with-lease'; then
  ASK "⚠️ git push --force 감지 — 원격 히스토리를 덮어써 동료/CI의 커밋을 날릴 수 있습니다. --force-with-lease가 더 안전합니다. 정말 강제 push할까요?"
fi

if match 'git[[:space:]]+([^&|;]*[[:space:]])?clean[[:space:]]+-[a-z]*f'; then
  ASK "⚠️ git clean -f 감지 — 추적되지 않은 파일이 영구 삭제됩니다(휴지통 없음). 먼저 git clean -n으로 대상을 확인했나요? 실행할까요?"
fi

if match 'git[[:space:]]+([^&|;]*[[:space:]])?branch[[:space:]]+([^&|;]*[[:space:]])?-D'; then
  ASK "⚠️ git branch -D 감지 — 병합되지 않은 브랜치를 강제 삭제합니다. 작업이 유실될 수 있습니다. 실행할까요?"
fi

if match 'git[[:space:]]+([^&|;]*[[:space:]])?(checkout|switch)[[:space:]]+([^&|;]*[[:space:]])?(-f|--force)'; then
  ASK "⚠️ git checkout/switch --force 감지 — 작업 트리의 미저장 변경이 버려집니다. 실행할까요?"
fi

# 작업 트리 변경 폐기: `git checkout .` / `git checkout -- <path>` / `git restore <path>`(--staged 제외)
if match 'git[[:space:]]+checkout[[:space:]]+([^&|;]*[[:space:]])?(--[[:space:]]|\.([[:space:]/]|$))'; then
  ASK "⚠️ git checkout으로 작업 트리 변경 폐기 감지 — 저장 안 한 수정이 사라집니다. 실행할까요?"
fi
if match 'git[[:space:]]+restore([[:space:]]|$)' && ! match '\-\-staged'; then
  ASK "⚠️ git restore 감지 — 작업 트리의 미저장 변경이 폐기됩니다. 실행할까요?"
fi

ALLOW
