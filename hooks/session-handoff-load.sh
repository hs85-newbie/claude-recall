#!/usr/bin/env bash
# session-handoff-load.sh — SessionStart 훅 (source=startup 전용)
#
# WHY: 추적된 스킬 사용의 95%가 context save/restore였다(2026-06-26 분석).
#      그중 restore는 "세션 시작 때마다 손으로 직전 맥락을 다시 불러오는" 반복 작업이다.
#      이 훅은 현재 작업 폴더(레포)의 가장 최근 세션 인계 메모를 자동으로 컨텍스트에 주입해
#      수동 restore 단계를 없앤다.
#
# 동작: stdin(JSON)의 cwd로 레포 루트를 구해 ~/.claude/handoffs/<레포>/ 의 최신 .md를
#       additionalContext로 출력. 없으면 조용히 통과.
# 철칙: fail-open — 어떤 오류에도 세션 시작을 막지 않는다(항상 exit 0).
#       단정 방지 — 주입 메모엔 "직전 세션 기록이며 사실로 단정 말고 확인하라"는 프레이밍을 붙인다.
set -uo pipefail

PASS() { exit 0; }   # 아무 출력 없이 통과

INPUT=$(cat 2>/dev/null) || PASS
[ -n "$INPUT" ] || PASS

HANDOFF_ROOT="${HOME}/.claude/handoffs"

INPUT_JSON="$INPUT" python3 - "$HANDOFF_ROOT" <<'PY' 2>/dev/null || PASS
import sys, json, os, glob

handoff_root = sys.argv[1]
try:
    d = json.loads(os.environ["INPUT_JSON"])  # stdin은 heredoc이 차지하므로 입력은 env로 전달
except Exception:
    sys.exit(0)

# startup 일 때만 주입(resume/clear/compact는 맥락이 이미 있거나 사용자가 새로 시작하려는 의도)
if (d.get("source") or "") != "startup":
    sys.exit(0)

cwd = d.get("cwd") or os.getcwd()

# 레포 루트 추정(없으면 cwd). git 디렉터리를 위로 탐색.
def repo_root(path):
    p = os.path.abspath(path)
    while True:
        if os.path.isdir(os.path.join(p, ".git")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return os.path.abspath(path)
        p = parent

root = repo_root(cwd)
key = root.strip("/").replace("/", "__")
folder = os.path.join(handoff_root, key)
files = sorted(glob.glob(os.path.join(folder, "*.md")))
if not files:
    sys.exit(0)

latest = files[-1]
try:
    with open(latest, "r", encoding="utf-8") as f:
        body = f.read().strip()
except Exception:
    sys.exit(0)
if not body:
    sys.exit(0)

note = (
    "아래는 이 작업 폴더(%s)의 **직전 세션 인계 메모**다.\n"
    "- 사용자가 이어서 작업하려는 경우에만 참고하라. 무관한 새 작업이면 무시하라.\n"
    "- 이 메모는 과거 시점 기록이다. 내용을 현재 사실로 단정하지 말고, 필요하면 현재 상태(git·파일)를 직접 확인한 뒤 진행하라.\n\n"
    "----- 직전 세션 인계 -----\n%s\n----- 인계 끝 -----"
) % (root, body)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": note,
    }
}, ensure_ascii=False))
PY
exit 0
