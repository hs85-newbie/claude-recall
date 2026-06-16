#!/usr/bin/env bash
# 로컬 LLM 호출 전 시스템 부하 검사 — 여유 생길 때까지 대기, 타임아웃 시 클라우드 폴백
# 메모리 측정: vm_stat 기반 (memory_pressure 명령은 macOS 버전별 포맷 불일치)
set -euo pipefail

THRESHOLD_CPU_IDLE=${LOCAL_LLM_CPU_IDLE_THRESHOLD:-40}
THRESHOLD_MEM_FREE=${LOCAL_LLM_MEM_FREE_THRESHOLD:-8}    # 기본 8% (LLM 로드 상태 고려)
MAX_WAIT_SEC=${LOCAL_LLM_MAX_WAIT:-180}
WAIT_INTERVAL=${LOCAL_LLM_WAIT_INTERVAL:-15}
CORE_COUNT=$(sysctl -n hw.ncpu)
LOAD_LIMIT=$(awk -v c="$CORE_COUNT" 'BEGIN{print c*0.7}')

check_load() {
  # CPU idle (top 한 번 샘플링)
  local idle_pct
  idle_pct=$(top -l 1 -n 0 2>/dev/null | awk '/CPU usage/ {gsub("%",""); print $7}')
  local idle_int="${idle_pct%.*}"
  [ -z "$idle_int" ] && idle_int=100

  # Load average (1분)
  local load1
  load1=$(sysctl -n vm.loadavg | awk '{print $2}')

  # 메모리 가용률 — vm_stat로 계산 (페이지 단위)
  # 가용 = free + inactive (inactive는 즉시 회수 가능)
  local free_p inact_p act_p wired_p total mem_free
  free_p=$(vm_stat | awk '/Pages free/ {gsub("\\.",""); print $3+0; exit}')
  inact_p=$(vm_stat | awk '/Pages inactive/ {gsub("\\.",""); print $3+0; exit}')
  act_p=$(vm_stat | awk '/Pages active/ {gsub("\\.",""); print $3+0; exit}')
  wired_p=$(vm_stat | awk '/Pages wired down/ {gsub("\\.",""); print $4+0; exit}')
  total=$(( ${free_p:-0} + ${inact_p:-0} + ${act_p:-0} + ${wired_p:-0} ))
  if [ "$total" -gt 0 ]; then
    mem_free=$(( (${free_p:-0} + ${inact_p:-0}) * 100 / total ))
  else
    mem_free=50
  fi

  # 판정
  awk -v i="$idle_int" -v t="$THRESHOLD_CPU_IDLE" \
      -v l="$load1" -v ll="$LOAD_LIMIT" \
      -v m="$mem_free" -v mt="$THRESHOLD_MEM_FREE" '
    BEGIN {
      if (i >= t && l <= ll && m >= mt) {
        printf "OK idle=%s%% load=%s mem_free=%s%%\n", i, l, m
        exit 0
      } else {
        printf "BUSY idle=%s%% load=%s mem_free=%s%%\n", i, l, m
        exit 1
      }
    }'
}

# LM Studio 응답 확인 (폴백 판단)
if ! curl -sf http://localhost:1234/v1/models > /dev/null 2>&1; then
  echo "LOCAL_LLM_DOWN: LM Studio 서버 응답 없음 — 클라우드로 폴백"
  exit 2
fi

elapsed=0
while [ "$elapsed" -lt "$MAX_WAIT_SEC" ]; do
  if result=$(check_load); then
    echo "GO: $result (waited ${elapsed}s)"
    exit 0
  else
    echo "WAIT: $result (elapsed=${elapsed}s, next check in ${WAIT_INTERVAL}s)"
    sleep "$WAIT_INTERVAL"
    elapsed=$((elapsed + WAIT_INTERVAL))
  fi
done

echo "TIMEOUT: 시스템 부하 지속 ${MAX_WAIT_SEC}s — 클라우드로 폴백 권장"
exit 3
