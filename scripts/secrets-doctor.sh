#!/usr/bin/env bash
# secrets-doctor.sh — 필요 시크릿/자격증명 부재를 탐지·안내한다.
#
# 철학(P6): 시크릿은 운반하지 않는다. 새 머신에서 무엇이 빠졌는지 알려주기만 한다.
# 설치를 차단하지 않는다(항상 exit 0) — 누락은 경고로만 표면화.
#
# 단독 실행 가능, bootstrap.sh가 끝에서 호출.
set -uo pipefail

ok()   { printf '\033[1;32m[doctor] OK\033[0m   %s\n' "$*"; }
miss() { printf '\033[1;33m[doctor] 누락\033[0m %s\n' "$*"; MISSING=$((MISSING+1)); }

MISSING=0
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VAULT_DIR="$HOME/llm-wiki"

echo "===== secrets doctor ====="

# 1. ANTHROPIC_API_KEY — env 또는 ~/.env 또는 레포 루트 .env
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  ok "ANTHROPIC_API_KEY (env)"
elif grep -qs "ANTHROPIC_API_KEY" "$HOME/.env" 2>/dev/null; then
  ok "ANTHROPIC_API_KEY (~/.env)"
elif grep -qs "ANTHROPIC_API_KEY" "$REPO/.env" 2>/dev/null; then
  ok "ANTHROPIC_API_KEY (레포 .env)"
else
  miss "ANTHROPIC_API_KEY — 요약 단계 실패함. 설정: export ANTHROPIC_API_KEY=sk-ant-... 또는 ~/.env / $REPO/.env 에 기록"
fi

# 2. git 커밋 신원 (vault commit에 필요)
if git config --get user.email >/dev/null 2>&1; then
  ok "git user.email ($(git config --get user.email))"
else
  miss "git user.email/user.name 미설정 — vault commit 실패. 설정: git config --global user.email you@x"
fi

# 3. vault(llm-wiki) 클론 + 원격 자격증명
if [ -d "$VAULT_DIR/.git" ]; then
  ok "vault 클론됨 ($VAULT_DIR)"
  if git -C "$VAULT_DIR" ls-remote >/dev/null 2>&1; then
    ok "vault 원격 접근(git 자격증명)"
  else
    miss "vault 원격 접근 실패 — git 자격증명(SSH 키/PAT) 확인. 크로스머신 push/pull 비활성"
  fi
else
  miss "vault 미클론 — 크로스머신 회상 비활성. export LLM_WIKI_REMOTE=git@... 후 ./bootstrap.sh, 또는 git clone <remote> $VAULT_DIR"
fi

echo "=========================="
if [ "$MISSING" -eq 0 ]; then
  ok "모든 시크릿/자격증명 준비됨"
else
  printf '\033[1;33m[doctor] %d개 누락 — 위 안내 참고(설치는 계속 진행됨)\033[0m\n' "$MISSING"
fi
exit 0
