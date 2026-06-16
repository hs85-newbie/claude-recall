#!/usr/bin/env bash
# setup-local-llm.sh — 머신 스펙(RAM/arch)을 감지해 LM Studio 로컬 LLM을 구성한다.
#
# 하는 일:
#   1. RAM·OS·아키텍처 감지
#   2. RAM 티어 → 권장 모델 선택 (LOCAL_LLM_MODEL env로 override 가능)
#   3. lms(LM Studio CLI)로 모델 다운로드 + 서버 기동 + 로드
#   4. ~/.claude/settings.json 의 local-llm MCP 모델명을 선택값으로 갱신
#
# 다운로드는 수 GB — opt-in 스크립트(bootstrap은 --local-llm 시에만 호출).
# 모델 미지정 시 RAM 기준 권장값을 쓰며, `LOCAL_LLM_MODEL=...` 로 강제 지정 가능.
set -uo pipefail

log() { printf '\033[1;36m[local-llm]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[local-llm]\033[0m %s\n' "$*"; }

# ── 1. 스펙 감지 ──
OS="$(uname -s)"; ARCH="$(uname -m)"
case "$OS" in
  Darwin) RAM_GB=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 )) ;;
  Linux)  RAM_GB=$(awk '/MemTotal/{print int($2/1024/1024)}' /proc/meminfo) ;;
  *) warn "미지원 OS($OS)"; exit 1 ;;
esac
# Apple Silicon은 MLX 런타임이 최적
FMT_FLAG=""
[ "$OS" = "Darwin" ] && [ "$ARCH" = "arm64" ] && FMT_FLAG="--mlx"
log "감지: $OS/$ARCH · RAM ${RAM_GB}GB · 포맷 ${FMT_FLAG:-auto}"

# ── 2. 모델 선택 (RAM 티어 → 권장값, env override 우선) ──
# WHY: 정확한 카탈로그 키는 버전별로 변하므로 `lms get -y` 퍼지 해석에 위임. 패밀리명만 지정.
if [ -n "${LOCAL_LLM_MODEL:-}" ]; then
  MODEL="$LOCAL_LLM_MODEL"
elif [ "$RAM_GB" -lt 16 ]; then
  MODEL="qwen/qwen3-4b"
elif [ "$RAM_GB" -lt 32 ]; then
  MODEL="google/gemma-4-26b-a4b"
elif [ "$RAM_GB" -lt 64 ]; then
  MODEL="qwen/qwen3-32b"
else
  MODEL="openai/gpt-oss-20b"
fi
log "선택 모델: $MODEL  (override: LOCAL_LLM_MODEL=... )"

# ── 3. lms 준비 ──
LMS="$(command -v lms || echo "$HOME/.lmstudio/bin/lms")"
if [ ! -x "$LMS" ]; then
  warn "lms(LM Studio CLI) 미설치 — https://lmstudio.ai 설치 후 재실행. (또는 ollama 등 대체)"
  exit 2
fi

log "모델 다운로드 (수 GB, 시간 소요)..."
if ! "$LMS" get "$MODEL" $FMT_FLAG -y; then
  warn "다운로드 실패 — 모델명 확인: lms get \"$MODEL\" 수동 실행 또는 LOCAL_LLM_MODEL 지정"
  exit 3
fi
"$LMS" server start >/dev/null 2>&1 || true
"$LMS" load "$MODEL" -y >/dev/null 2>&1 || warn "자동 로드 실패 — lms load \"$MODEL\" 수동 시도"

# ── 4. settings.json 모델명 반영 ──
# WHY: route-to-local 훅·local-llm MCP가 참조하는 LOCAL_LLM_MODEL을 선택값으로 동기화
SETTINGS="$HOME/.claude/settings.json"
if [ -f "$SETTINGS" ]; then
  python3 - "$SETTINGS" "$MODEL" <<'PY'
import json, sys
path, model = sys.argv[1], sys.argv[2]
d = json.load(open(path))
mcp = d.get("mcpServers", {}).get("local-llm")
if mcp:
    mcp.setdefault("env", {})["LOCAL_LLM_MODEL"] = model
    json.dump(d, open(path, "w"), indent=2, ensure_ascii=False)
    open(path, "a").write("\n")
    print("UPDATED")
else:
    print("NO_LOCAL_LLM_MCP")
PY
  log "settings.json LOCAL_LLM_MODEL=$MODEL 반영"
else
  warn "$SETTINGS 없음 — bootstrap.sh 먼저 실행 권장"
fi

log "완료. 확인: $LMS ps  ·  curl -s http://localhost:1234/v1/models"
