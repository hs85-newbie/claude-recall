#!/bin/bash
# obsidian-memory-pipeline 통합 실행 wrapper
# launchd가 04:00에 호출, ingest → summarize → export 순차 실행
# 각 단계 stdout에 [INGEST]·[SUMMARIZE]·[EXPORT] prefix 부여
# ingest 실패 시 후속 단계 차단 (exit code 즉시 전파)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BIN="$ROOT/.venv/bin/session-archive"
LOG="$HOME/.claude-archive/launchd-pipeline.log"

cd "$ROOT"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

run_stage() {
    local tag="$1"; shift
    local prefix="[$tag]"
    echo "$prefix start $(ts)"
    if "$@" 2>&1 | sed "s/^/$prefix /"; then
        echo "$prefix done  $(ts)"
        return 0
    else
        local rc=${PIPESTATUS[0]}
        echo "$prefix FAIL  rc=$rc $(ts)"
        return "$rc"
    fi
}

{
    echo "===== pipeline start $(ts) ====="

    if ! run_stage INGEST "$BIN" ingest; then
        echo "===== pipeline abort: INGEST failed $(ts) ====="
        exit 1
    fi

    if ! run_stage SUMMARIZE "$BIN" summarize --mode haiku-only; then
        echo "[WARN] SUMMARIZE failed — continuing to EXPORT"
    fi

    if ! run_stage EXPORT "$BIN" export-obsidian; then
        echo "[WARN] EXPORT failed"
    fi

    if ! run_stage CHECKPOINTS "$BIN" ingest-checkpoints; then
        echo "[WARN] CHECKPOINTS failed"
    fi

    # 크로스머신 운반: 로컬 export를 vault에 송신(push) → 타 머신 변경 수신(pull).
    # 순서 = export→push→pull (단일 작성자 불변식상 로컬 export가 pull에 덮이지 않게).
    if ! run_stage VAULT_PUSH "$BIN" vault-push; then
        echo "[WARN] VAULT_PUSH failed"
    fi

    if ! run_stage VAULT_SYNC "$BIN" sync-vault; then
        echo "[WARN] VAULT_SYNC failed"
    fi

    echo "===== pipeline end $(ts) ====="
} >> "$LOG" 2>&1
