#!/usr/bin/env bash
# bootstrap.sh — 신규 시스템에서 Claude Code 전역 환경을 한 번에 구성한다.
#
# 제공물: 전역 규칙(CLAUDE.md) · 설정(settings.json) · hooks · custom agents ·
#         session-archive 스킬 · gstack 스킬 · im-not-ai(Humanize KR) · RAG ingest
#
# 사용법:
#   git clone https://github.com/hs85-newbie/claude-recall.git
#   cd claude-recall && ./bootstrap.sh [--local-llm]
#
#   --local-llm : 머신 스펙 감지 → 로컬 LLM(LM Studio) 모델 다운로드·구성 (opt-in, 수 GB)
#
# 멱등(idempotent): 여러 번 실행해도 안전. 기존 설정은 백업 후 갱신.
set -euo pipefail

WANT_LOCAL_LLM=0
[ "${1:-}" = "--local-llm" ] && WANT_LOCAL_LLM=1

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
TS="$(date +%Y%m%d-%H%M%S)"

log() { printf '\033[1;36m[bootstrap]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[bootstrap]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[bootstrap]\033[0m %s\n' "$*"; }

# ── 0. 전제조건 체크 ──
# WHY: 빠진 도구를 설치 도중이 아니라 시작 시점에 한 번에 알려준다.
check_prereqs() {
  local missing=0
  for c in git python3 bun node claude; do
    if ! command -v "$c" >/dev/null 2>&1; then
      err "필수 도구 없음: $c"
      missing=1
    fi
  done
  # Python 3.11+ 요구 (session-archive)
  if command -v python3 >/dev/null 2>&1; then
    if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      err "python3 3.11+ 필요 (현재: $(python3 -V 2>&1))"
      missing=1
    fi
  fi
  if [ "$missing" = 1 ]; then
    cat <<'GUIDE'

[bootstrap] 전제조건 미충족 — 아래 설치 후 다시 실행하세요.
  git      : https://git-scm.com  (또는 macOS: xcode-select --install)
  python3  : 3.11+  (macOS: brew install python@3.12 / Linux: apt install python3)
  bun      : curl -fsSL https://bun.sh/install | bash   (gstack 빌드용)
  node     : https://nodejs.org   (또는 brew install node)
  claude   : Claude Code CLI 설치 + 로그인 (claude /login)
GUIDE
    exit 1
  fi
  log "전제조건 충족 (git·python3.11+·bun·node·claude)"
}
check_prereqs

mkdir -p "$CLAUDE_DIR/hooks" "$CLAUDE_DIR/agents" "$CLAUDE_DIR/skills"

# ── 1. CLAUDE.md 심링크 ──
# WHY: 규칙 단일 진실 소스 — 레포 수정이 즉시 반영되도록 복사가 아닌 심링크
if [ -L "$CLAUDE_DIR/CLAUDE.md" ] && [ "$(readlink "$CLAUDE_DIR/CLAUDE.md")" = "$REPO/CLAUDE.md" ]; then
  log "CLAUDE.md 심링크 이미 정상"
else
  [ -e "$CLAUDE_DIR/CLAUDE.md" ] && mv "$CLAUDE_DIR/CLAUDE.md" "$CLAUDE_DIR/CLAUDE.md.bak-$TS"
  ln -sf "$REPO/CLAUDE.md" "$CLAUDE_DIR/CLAUDE.md"
  log "CLAUDE.md → $REPO/CLAUDE.md 심링크 생성"
fi

# ── 2. settings.json 렌더링 (__HOME__ 치환 + 부재 의존성 제외) ──
# WHY: settings.local.json(머신별 누적 권한)은 별도 파일이라 건드리지 않음
[ -e "$CLAUDE_DIR/settings.json" ] && cp "$CLAUDE_DIR/settings.json" "$CLAUDE_DIR/settings.json.bak-$TS"
python3 - "$REPO/settings.json" "$HOME" "$CLAUDE_DIR/settings.json" <<'PY'
import json, os, sys
src, home, dst = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.loads(json.dumps(json.load(open(src))).replace("__HOME__", home))
# 외부 의존(gemma4-bench)이 없으면 local-llm MCP 제거 — 깨진 MCP 로드 방지
mcp = data.get("mcpServers", {})
llm = mcp.get("local-llm")
if llm and not os.path.exists(llm["args"][0]):
    mcp.pop("local-llm", None)
    if not mcp:
        data.pop("mcpServers", None)
    print("SKIP_LOCAL_LLM")
with open(dst, "w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
PY
log "settings.json 렌더링 완료 (백업: settings.json.bak-$TS)"

# ── 3. hooks 배치 ──
cp "$REPO"/hooks/*.sh "$CLAUDE_DIR/hooks/"
chmod +x "$CLAUDE_DIR"/hooks/*.sh
log "hooks 배치: $(ls "$REPO"/hooks/*.sh | wc -l | tr -d ' ')개"

# ── 4. custom agents 배치 (기존 타 agent는 보존) ──
cp "$REPO"/agents/*.md "$CLAUDE_DIR/agents/"
log "custom agents 배치: $(ls "$REPO"/agents/*.md | wc -l | tr -d ' ')개"

# ── 5. session-archive 스킬 배치 (심링크 — 레포 수정 라이브 반영) ──
# WHY: ln -sfn 은 기존 심링크/디렉토리 dest를 멱등 교체 (-n: 심링크-to-dir 미참조)
for s in "$REPO"/skills/*/; do
  name="$(basename "${s%/}")"
  ln -sfn "${s%/}" "$CLAUDE_DIR/skills/$name"
done
log "session-archive 스킬 배치"

# ── 6. gstack 스킬 설치 ──
if [ ! -f "$HOME/gstack/setup" ]; then
  log "gstack clone..."
  git clone https://github.com/garrytan/gstack.git "$HOME/gstack"
fi
( cd "$HOME/gstack" && ./setup --host claude ) && log "gstack 스킬 설치 완료"

# ── 7. im-not-ai (Humanize KR, 선택) ──
# 한글 humanize 스킬·에이전트. IM_NOT_AI_REMOTE 설정 시에만 설치(미설정·접근불가 시 생략).
if [ -f "$HOME/im-not-ai/install.sh" ]; then
  ( cd "$HOME/im-not-ai" && ./install.sh --claude-only ) && log "im-not-ai(Humanize KR) 설치 완료"
elif [ -n "${IM_NOT_AI_REMOTE:-}" ]; then
  log "im-not-ai clone: $IM_NOT_AI_REMOTE"
  if git clone "$IM_NOT_AI_REMOTE" "$HOME/im-not-ai"; then
    ( cd "$HOME/im-not-ai" && ./install.sh --claude-only ) && log "im-not-ai 설치 완료"
  else
    warn "im-not-ai clone 실패 — 생략(humanize 스킬 없이 진행)"
  fi
else
  log "im-not-ai 생략 — 쓰려면 export IM_NOT_AI_REMOTE=git@... 후 재실행"
fi

# ── 8. RAG/obsidian ingest 파이프라인 ──
SA_SETUP="$REPO/tools/session-archive/scripts/setup.sh"
if [ -x "$SA_SETUP" ]; then
  log "session-archive 파이프라인 설치 위임..."
  "$SA_SETUP" || warn "session-archive setup 일부 실패 — 위 로그 확인"
else
  warn "tools/session-archive/scripts/setup.sh 없음 — RAG 파이프라인 수동 설치 필요"
fi

# ── 8.5. vault(llm-wiki) 클론 — 크로스머신 회상 소스 ──
# 시크릿 운반은 안 함(P6) — vault repo만 클론, git 자격증명은 사용자 기존 설정 사용.
# LLM_WIKI_REMOTE 미설정 시 생략(RAG 로컬 전용 모드).
VAULT_DIR="$HOME/llm-wiki"
if [ -n "${LLM_WIKI_REMOTE:-}" ] && [ ! -d "$VAULT_DIR/.git" ]; then
  log "vault 클론: $LLM_WIKI_REMOTE → $VAULT_DIR"
  if git clone "$LLM_WIKI_REMOTE" "$VAULT_DIR" 2>/dev/null; then
    log "vault 클론 완료"
  else
    warn "vault 클론 실패 — git 자격증명 확인(crossmachine 회상 비활성). 수동: git clone $LLM_WIKI_REMOTE $VAULT_DIR"
  fi
elif [ -d "$VAULT_DIR/.git" ]; then
  log "vault 이미 존재: $VAULT_DIR"
else
  log "vault 생략 — LLM_WIKI_REMOTE 미설정(크로스머신 회상 쓰려면 export LLM_WIKI_REMOTE=git@...)"
fi

# ── 9. 로컬 LLM (opt-in) ──
LLM_SETUP="$REPO/scripts/setup-local-llm.sh"
if [ "$WANT_LOCAL_LLM" = 1 ] && [ -x "$LLM_SETUP" ]; then
  log "로컬 LLM 스펙 감지·구성 위임..."
  "$LLM_SETUP" || warn "로컬 LLM setup 일부 실패 — 위 로그 확인"
else
  log "로컬 LLM 구성 생략 — 원하면 별도 실행: scripts/setup-local-llm.sh (또는 ./bootstrap.sh --local-llm)"
fi

# ── 10. secrets doctor — 필요 시크릿/자격증명 부재 점검(차단 안 함) ──
DOCTOR="$REPO/scripts/secrets-doctor.sh"
[ -x "$DOCTOR" ] && "$DOCTOR" || true

# ── 완료 ──
log "완료"
cat <<'DONE'
다음 작업:
  1. ANTHROPIC_API_KEY 설정 (요약 단계 필요):
       export ANTHROPIC_API_KEY=sk-ant-...   또는   ~/.env 에 기록
  2. (선택) 로컬 LLM 사용 시 LM Studio 실행 + ~/gemma4-bench 클론
  3. Claude Code 재시작 → 설정/스킬/에이전트 로드 확인
DONE
