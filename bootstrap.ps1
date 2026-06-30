# bootstrap.ps1 — 신규 윈도우에서 claude-recall 전역 환경을 구성한다 (네이티브, WSL 불필요).
#
# 제공물: 전역 규칙(CLAUDE.md) · 설정(settings.json, 병합) · hooks(.ps1) · custom agents ·
#         skills · session-archive(회상·검색·대시보드) · 로컬 LLM MCP(LM Studio)
# 제외:   gstack · im-not-ai(외부 bash) · launchd/cron(맥/리눅스 전용)
#
# 사용법 (pwsh 7 권장):
#   git clone https://github.com/hs85-newbie/claude-recall.git
#   cd claude-recall ; ./bootstrap.ps1
#
# 멱등: 여러 번 실행해도 안전. settings.json은 덮어쓰지 않고 병합(기존 키·권한 보존, 백업 생성).
# 철칙: settings.local.json(머신별 누적 권한)은 절대 건드리지 않는다.

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$REPO = $PSScriptRoot
$CLAUDE_DIR = Join-Path $HOME '.claude'
$TS = Get-Date -Format 'yyyyMMdd-HHmmss'

function Log  ($m) { Write-Host "[bootstrap] $m" -ForegroundColor Cyan }
function Warn ($m) { Write-Host "[bootstrap] $m" -ForegroundColor Yellow }
function Err  ($m) { Write-Host "[bootstrap] $m" -ForegroundColor Red }

# ── python 실행기 탐색 (python / python3 / py 런처) ──
# WHY: 윈도우는 (1) python.org 설치본이 `py` 런처로만 PATH에 잡히거나
#      (2) Microsoft Store 별칭 스텁이 `python`을 가로채 빈 출력을 내는 경우가 흔하다.
#      세 후보를 모두 시도하고, 버전·실제 실행경로(sys.executable)를 한 번에 받아 검증한다.
function Find-Python {
    $probe = 'import sys; print(str(sys.version_info[0])+"."+str(sys.version_info[1])); print(sys.executable)'
    # @{exe; pre} — py 런처는 -3 옵션으로 파이썬3 강제.
    # py를 첫 후보로: Store 별칭 stub(python.exe)이 멈추거나 빈 출력 내는 경우를 회피.
    $candidates = @(
        @{ exe = 'py';      pre = @('-3') },
        @{ exe = 'python';  pre = @() },
        @{ exe = 'python3'; pre = @() }
    )
    foreach ($c in $candidates) {
        $cmd = Get-Command $c.exe -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            $out = & $cmd.Source @($c.pre + @('-c', $probe)) 2>$null
            if (-not $out) { continue }   # Store 스텁 등 빈 출력 → 다음 후보
            $ver = ([string]($out | Select-Object -First 1)).Trim()
            $exe = ([string]($out | Select-Object -Last 1)).Trim()
            if ($ver -match '^(\d+)\.(\d+)') {
                $maj = [int]$Matches[1]; $min = [int]$Matches[2]
                if ((($maj -gt 3) -or ($maj -eq 3 -and $min -ge 11)) -and $exe) {
                    return $exe   # 실제 python.exe 절대경로 반환(런처/별칭 모호성 제거)
                }
            }
        } catch { }
    }
    return $null
}

# ── 0. 전제조건 ──
Log '전제조건 점검...'
$PY = Find-Python
$missing = @()
if (-not (Get-Command git -ErrorAction SilentlyContinue))    { $missing += 'git' }
if (-not $PY)                                                 { $missing += 'python 3.11+' }
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { $missing += 'claude (Claude Code CLI)' }
if ($missing.Count -gt 0) {
    Err "필수 도구 없음: $($missing -join ', ')"
    Write-Host @'

[bootstrap] 아래 설치 후 다시 실행하세요.
  git    : https://git-scm.com/download/win   (또는 winget install Git.Git)
  python : 3.11+  https://python.org           (또는 winget install Python.Python.3.12)
  claude : npm install -g @anthropic-ai/claude-code  →  claude  (로그인)

  ※ "파이썬을 깔았는데도 위에 python이 뜬다"면 보통 PATH 문제입니다:
     - 설치 후 PowerShell 창을 새로 여세요(PATH 갱신).
     - `py -V` 가 되면 파이썬은 있는 겁니다(이 스크립트는 py 런처도 지원합니다).
     - `python` 입력 시 Microsoft Store가 열리면, 설정 > 앱 > 앱 실행 별칭에서
       python.exe/python3.exe 별칭을 끄세요(가짜 stub가 진짜 파이썬을 가립니다).
'@
    exit 1
}
Log "python: $PY"

# pwsh 7(훅 런타임) · node(로컬 LLM MCP)는 없어도 진행하되 경고
$hasPwsh = [bool](Get-Command pwsh -ErrorAction SilentlyContinue)
$hasNode = [bool](Get-Command node -ErrorAction SilentlyContinue)
if (-not $hasPwsh) { Warn 'pwsh(PowerShell 7) 없음 — 훅이 동작하려면 필요: winget install Microsoft.PowerShell' }
if (-not $hasNode) { Warn 'node 없음 — 로컬 LLM MCP에 필요(없으면 자동 제외): https://nodejs.org' }

# ── 1. 디렉터리 ──
foreach ($d in @('hooks', 'agents', 'skills', 'handoffs')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $CLAUDE_DIR $d) | Out-Null
}

# ── 2. CLAUDE.md (복사 — 윈도우 심링크 권한 회피. 재실행 시 갱신) ──
$dstClaude = Join-Path $CLAUDE_DIR 'CLAUDE.md'
if (Test-Path $dstClaude) { Copy-Item $dstClaude "$dstClaude.bak-$TS" -Force }
Copy-Item (Join-Path $REPO 'CLAUDE.md') $dstClaude -Force
Log 'CLAUDE.md 배치'

# ── 3. agents ──
Copy-Item (Join-Path $REPO 'agents\*.md') (Join-Path $CLAUDE_DIR 'agents') -Force
Log "agents 배치: $((Get-ChildItem (Join-Path $REPO 'agents\*.md')).Count)개"

# ── 4. skills (디렉터리 통째 복사) ──
Get-ChildItem (Join-Path $REPO 'skills') -Directory | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $CLAUDE_DIR 'skills') -Recurse -Force
}
Log 'skills 배치'

# ── 5. hooks (.ps1만 — 윈도우 전용) ──
Copy-Item (Join-Path $REPO 'hooks\*.ps1') (Join-Path $CLAUDE_DIR 'hooks') -Force
Log "hooks(.ps1) 배치: $((Get-ChildItem (Join-Path $REPO 'hooks\*.ps1')).Count)개"

# ── 6. session-archive 설치 (venv + editable) — settings 렌더 전에 (MCP exe 존재시켜야 함) ──
$saDir = Join-Path $REPO 'tools\session-archive'
$venvPy = Join-Path $saDir '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    Log 'session-archive venv 생성...'
    & $PY -m venv (Join-Path $saDir '.venv')
}
Log 'session-archive 설치(pip install -e .)...'
& $venvPy -m pip install -q --upgrade pip
& $venvPy -m pip install -q -e $saDir
Log 'session-archive 설치 완료'

# ── 7. settings.json 렌더 + 병합 (기존 값 보존) ──
$dstSettings = Join-Path $CLAUDE_DIR 'settings.json'
if (Test-Path $dstSettings) {
    Copy-Item $dstSettings "$dstSettings.bak-$TS" -Force
    Log "기존 settings.json 백업: settings.json.bak-$TS"
}
$mergePy = @'
import json, os, sys
repo_settings, home, claude_dir, repo_dir, existing_path, out_path = sys.argv[1:7]
tpl = json.load(open(repo_settings, encoding="utf-8"))

# 훅 명령 → 윈도우 pwsh + .ps1 로 치환
for ev, arr in (tpl.get("hooks") or {}).items():
    for entry in arr:
        for h in entry.get("hooks", []):
            cmd = h.get("command", "")
            if cmd.endswith(".sh"):
                name = os.path.basename(cmd).replace(".sh", ".ps1")
                target = os.path.join(claude_dir, "hooks", name)
                h["command"] = 'pwsh -NoProfile -ExecutionPolicy Bypass -File "%s"' % target

# MCP 서버 경로 → 윈도우
mcp = tpl.get("mcpServers") or {}
if "local-llm" in mcp:
    p = os.path.join(home, "gemma4-bench", "scripts", "mcp-local-llm.mjs")
    mcp["local-llm"]["args"] = [p]
    # gemma4-bench 미클론이면 깨진 MCP 로드 방지를 위해 제외 (클론 후 재실행하면 포함됨)
    if not os.path.exists(p):
        mcp.pop("local-llm", None); print("SKIP_LOCAL_LLM")
if "session-archive-recall" in mcp:
    mcp["session-archive-recall"]["command"] = os.path.join(
        repo_dir, "tools", "session-archive", ".venv", "Scripts", "session-archive-mcp.exe")
if mcp: tpl["mcpServers"] = mcp
else: tpl.pop("mcpServers", None)

# 남은 __HOME__ 치환
def repl(o):
    if isinstance(o, str): return o.replace("__HOME__", home)
    if isinstance(o, list): return [repl(x) for x in o]
    if isinstance(o, dict): return {k: repl(v) for k, v in o.items()}
    return o
tpl = repl(tpl)

# 기존 설정 로드 후 병합 (기존이 베이스, 템플릿을 덮어씀 / 배열은 합집합 / 기존-only 키 보존)
existing = {}
if os.path.exists(existing_path):
    try: existing = json.load(open(existing_path, encoding="utf-8"))
    except Exception: existing = {}

# 템플릿이 소유하는 항목은 병합 전 기존에서 제거 → 윈도우 버전만 반영(맥 .sh 훅·관리 MCP 중복/잔존 방지).
# 사용자 커스텀 훅(.ps1 등)·기타 MCP·일반 키는 건드리지 않아 보존된다.
def strip_managed(s):
    hooks = s.get("hooks")
    if isinstance(hooks, dict):
        for ev in list(hooks.keys()):
            kept = []
            for entry in hooks[ev]:
                inner = [h for h in entry.get("hooks", []) if not str(h.get("command", "")).endswith(".sh")]
                if inner:
                    entry["hooks"] = inner; kept.append(entry)
            if kept: hooks[ev] = kept
            else: del hooks[ev]
        if not hooks: s.pop("hooks", None)
    m = s.get("mcpServers")
    if isinstance(m, dict):
        for name in ("local-llm", "session-archive-recall"):
            m.pop(name, None)
        if not m: s.pop("mcpServers", None)
strip_managed(existing)

def merge(base, over):
    for k, v in over.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            merge(base[k], v)
        elif k in base and isinstance(base[k], list) and isinstance(v, list):
            for x in v:
                if x not in base[k]: base[k].append(x)
        else:
            base[k] = v
    return base

result = merge(existing, tpl)
open(out_path, "w", encoding="utf-8").write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
print("OK")
'@
$existingForMerge = if (Test-Path $dstSettings) { $dstSettings } else { '__none__' }
$mergeOut = $mergePy | & $PY - (Join-Path $REPO 'settings.json') $HOME $CLAUDE_DIR $REPO $existingForMerge $dstSettings
if ($mergeOut -match 'SKIP_LOCAL_LLM') { Warn 'local-llm MCP 제외됨 — ~/gemma4-bench 클론 후 bootstrap.ps1 재실행하면 포함됩니다.' }
if ($mergeOut -match 'OK') { Log 'settings.json 렌더+병합 완료 (기존 값 보존)' } else { Err "settings 병합 실패: $mergeOut" }

# ── 완료 ──
Log '완료'
Write-Host @"

다음 작업:
  1. Claude Code를 재시작 → 설정·스킬·에이전트 로드 확인
  2. (회사 API) Claude Code가 회사 엔드포인트를 쓰도록 환경변수 설정 — 기존 방식 그대로
  3. (로컬 LLM 쓰려면)
       - LM Studio(윈도우) 설치 → 모델 로드 → 서버 시작(localhost:1234)
       - git clone https://github.com/<your>/gemma4-bench  →  ~/gemma4-bench
       - node 설치 후  ./bootstrap.ps1  재실행 (local-llm MCP 포함)
  4. (요약 단계 쓰려면) `$Env:ANTHROPIC_API_KEY = "sk-ant-..."`
  5. 세션 회상·검색·대시보드:
       cd tools\session-archive
       .venv\Scripts\session-archive ingest
       .venv\Scripts\session-archive dashboard --open
"@
