#!/usr/bin/env bash
# setup.sh — session-archive RAG/obsidian 파이프라인을 신규 시스템에 설치한다.
#
# 하는 일:
#   1. Python venv 생성 + 패키지 설치 (editable)
#   2. ~/.claude-archive 상태 디렉토리 생성
#   3. OS 감지 → 매일 04:00 스케줄 등록 (macOS=launchd / Linux=cron)
#
# macOS / Linux 공통. 사용자명·경로 하드코딩 없음 ($HOME·스크립트 위치 기반).
# bootstrap.sh가 호출하거나 단독 실행 가능.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE_DIR="$HOME/.claude-archive"
BIN="$ROOT/.venv/bin/session-archive"

log() { printf '\033[1;36m[session-archive setup]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[session-archive setup]\033[0m %s\n' "$*"; }

# ── 1. venv + 패키지 ──
if [ ! -x "$BIN" ]; then
  log "venv 생성 + 패키지 설치..."
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install -q -e "$ROOT"
fi
log "CLI 준비: $BIN"

# ── 2. 상태 디렉토리 ──
mkdir -p "$ARCHIVE_DIR"

# ── 3. 스케줄러 등록 (OS 분기) ──
OS="$(uname -s)"
case "$OS" in
  Darwin)
    PLIST="$HOME/Library/LaunchAgents/com.session-archive.obsidian-memory-pipeline.plist"
    mkdir -p "$HOME/Library/LaunchAgents"
    sed -e "s|__ROOT__|$ROOT|g" -e "s|__HOME__|$HOME|g" \
        "$ROOT/scripts/obsidian-memory-pipeline.plist.template" > "$PLIST"
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    log "launchd 등록 완료 (매일 04:00): $PLIST"
    ;;
  Linux)
    CRON_LINE="0 4 * * * $ROOT/scripts/pipeline.sh"
    # WHY: 멱등 — 기존 동일 라인 제거 후 재등록
    ( crontab -l 2>/dev/null | grep -vF "$ROOT/scripts/pipeline.sh" || true; echo "$CRON_LINE" ) | crontab -
    log "cron 등록 완료 (매일 04:00): $CRON_LINE"
    ;;
  *)
    warn "미지원 OS($OS) — 스케줄러 수동 등록 필요: $ROOT/scripts/pipeline.sh 를 매일 04:00 실행"
    ;;
esac

# ── 검증 ──
if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ ! -f "$HOME/.env" ]; then
  warn "ANTHROPIC_API_KEY 미설정 — summarize 단계 실패함. export ANTHROPIC_API_KEY=... 또는 ~/.env 기록"
fi
log "설치 완료. 수동 1회 실행: $ROOT/scripts/pipeline.sh"
