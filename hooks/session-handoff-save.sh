#!/usr/bin/env bash
# session-handoff-save.sh — SessionEnd 훅 (모든 reason)
#
# WHY: 세션이 끝나면 "무엇을 하던 중이었는지"가 휘발한다(하용호 '의도부채'의 개인판).
#      다음 세션 시작 때 session-handoff-load.sh가 이 기록을 자동 주입해 수동 restore를 없앤다.
#      LLM 없이 git 상태 + 마지막 사용자 프롬프트 + transcript 포인터만 기계적으로 남긴다
#      (실패 표면 최소화). 더 풍부한 인계가 필요하면 /handoff 스킬로 덮어쓴다.
#
# 동작: ~/.claude/handoffs/<레포>/<타임스탬프>.md 로 저장(덮어쓰기 없음 → 손실 0). 최신 10개만 보존.
# 철칙: fail-open — 어떤 오류에도 세션 종료를 막지 않는다(항상 exit 0). 사소한 세션은 저장 생략.
set -uo pipefail

DONE() { exit 0; }

INPUT=$(cat 2>/dev/null) || DONE
[ -n "$INPUT" ] || DONE

HANDOFF_ROOT="${HOME}/.claude/handoffs"
TS=$(date +%Y%m%d-%H%M%S 2>/dev/null) || DONE

INPUT_JSON="$INPUT" python3 - "$HANDOFF_ROOT" "$TS" <<'PY' 2>/dev/null || DONE
import sys, json, os, glob, subprocess

handoff_root, ts = sys.argv[1], sys.argv[2]
try:
    d = json.loads(os.environ["INPUT_JSON"])  # stdin은 heredoc이 차지하므로 입력은 env로 전달
except Exception:
    sys.exit(0)

cwd = d.get("cwd") or os.getcwd()
transcript_path = d.get("transcript_path") or ""
reason = d.get("reason") or "other"

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

# --- transcript에서 마지막 사용자 프롬프트 수집(최대 6개, verbatim) ---
def extract_user_prompts(path, limit=6):
    out = []
    if not path or not os.path.exists(path):
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("type") != "user":
            continue
        msg = obj.get("message") or {}
        content = msg.get("content")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    parts.append(b.get("text", ""))
                # tool_result 등은 건너뜀(사용자 발화 아님)
            text = "\n".join(parts)
        text = (text or "").strip()
        # 시스템/훅 주입·메타 라인 제외
        if not text:
            continue
        if text.startswith("<") or "[SYSTEM NOTIFICATION" in text or "system-reminder" in text:
            continue
        out.append(text)
    return out[-limit:]

prompts = extract_user_prompts(transcript_path)
if not prompts:
    sys.exit(0)  # 사용자 발화 없는 세션 → 저장 생략

# --- git 상태 ---
def git(args):
    try:
        return subprocess.run(["git", "-C", root] + args, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""

branch = git(["branch", "--show-current"]) or "(unknown)"
status = git(["status", "-s"])
recent = git(["log", "--oneline", "-5"])

def trunc(s, n=400):
    s = s.strip()
    return s if len(s) <= n else s[:n] + " …(생략)"

lines = []
lines.append("# 세션 인계 메모 (자동 저장)")
lines.append("")
lines.append(f"- 저장 시각: {ts} · 종료 사유: {reason}")
lines.append(f"- 레포: {root}")
lines.append(f"- 브랜치: {branch}")
lines.append(f"- transcript: {transcript_path}")
lines.append("")
lines.append("## 마지막 사용자 지시(최신순 아님, 시간순)")
for p in prompts:
    lines.append(f"- {trunc(p)}")
lines.append("")
lines.append("## git 상태(종료 시점)")
lines.append("```")
lines.append("변경:")
lines.append(status if status else "(working tree clean)")
lines.append("")
lines.append("최근 커밋:")
lines.append(recent if recent else "(none)")
lines.append("```")
lines.append("")
lines.append("> 기계 자동 기록이다. 더 정확한 인계가 필요하면 다음 세션에서 transcript를 확인하거나, 작업 중 /handoff 로 풍부본을 남겨라.")

folder = os.path.join(handoff_root, key)
try:
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"{ts}.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
except Exception:
    sys.exit(0)

# --- 최신 10개만 보존 ---
try:
    files = sorted(glob.glob(os.path.join(folder, "*.md")))
    for old in files[:-10]:
        try:
            os.remove(old)
        except Exception:
            pass
except Exception:
    pass
PY
exit 0
