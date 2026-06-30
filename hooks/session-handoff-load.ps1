# session-handoff-load.ps1 — SessionStart 훅 (source=startup 전용, Windows / pwsh 7)
#
# WHY: 세션 시작마다 직전 맥락을 손으로 불러오는 반복을 없앤다.
#      현재 작업 폴더(레포)의 최신 인계 메모를 additionalContext로 자동 주입한다.
#      session-handoff-load.sh(macOS/Linux)의 윈도우판.
#
# 동작: stdin(JSON)의 cwd로 레포 루트를 구해 ~/.claude/handoffs/<레포키>/ 의
#       최신 .md를 출력. 없으면 조용히 통과.
# 철칙: fail-open — 어떤 오류에도 세션 시작을 막지 않는다(exit 0).
#       단정 방지 — "직전 기록이며 사실로 단정 말고 확인하라" 프레이밍을 붙인다.

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 레포 루트 추정: .git 디렉터리를 위로 탐색(없으면 cwd). save와 동일 로직.
function Get-RepoRoot([string]$path) {
    try { $p = (Resolve-Path -LiteralPath $path -ErrorAction Stop).Path }
    catch { return $path }
    while ($true) {
        if (Test-Path -LiteralPath (Join-Path $p '.git')) { return $p }
        $parent = Split-Path -LiteralPath $p -Parent
        if (-not $parent -or $parent -eq $p) { return $p }
        $p = $parent
    }
}

# 레포 경로 → 폴더 키. load/save가 반드시 동일해야 함.
function Get-RepoKey([string]$root) {
    return ($root -replace '[:/\\]', '_').Trim('_')
}

try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }

    $d = $null
    try { $d = $raw | ConvertFrom-Json } catch { exit 0 }

    # startup 일 때만 주입(resume/clear/compact는 제외)
    $source = if ($d.source) { [string]$d.source } else { '' }
    if ($source -ne 'startup') { exit 0 }

    $cwd = if ($d.cwd) { [string]$d.cwd } else { (Get-Location).Path }
    $root = Get-RepoRoot $cwd
    $key = Get-RepoKey $root

    $folder = Join-Path (Join-Path $HOME '.claude\handoffs') $key
    if (-not (Test-Path -LiteralPath $folder)) { exit 0 }

    $latest = Get-ChildItem -LiteralPath $folder -Filter '*.md' -File -ErrorAction SilentlyContinue |
        Sort-Object Name | Select-Object -Last 1
    if (-not $latest) { exit 0 }

    $body = (Get-Content -LiteralPath $latest.FullName -Raw -Encoding utf8).Trim()
    if ([string]::IsNullOrWhiteSpace($body)) { exit 0 }

    $note = @"
아래는 이 작업 폴더($root)의 **직전 세션 인계 메모**다.
- 사용자가 이어서 작업하려는 경우에만 참고하라. 무관한 새 작업이면 무시하라.
- 이 메모는 과거 시점 기록이다. 내용을 현재 사실로 단정하지 말고, 필요하면 현재 상태(git·파일)를 직접 확인한 뒤 진행하라.

----- 직전 세션 인계 -----
$body
----- 인계 끝 -----
"@

    $payload = @{
        hookSpecificOutput = @{
            hookEventName     = 'SessionStart'
            additionalContext = $note
        }
    }
    [Console]::Out.Write(($payload | ConvertTo-Json -Compress -Depth 5))
    exit 0
} catch {
    exit 0
}
