# session-handoff-save.ps1 — SessionEnd 훅 (모든 reason, Windows / pwsh 7)
#
# WHY: 세션이 끝나면 "무엇을 하던 중이었는지"가 휘발한다. 다음 세션 시작 때
#      session-handoff-load.ps1이 이 기록을 자동 주입해 수동 restore를 없앤다.
#      LLM 없이 git 상태 + 마지막 사용자 프롬프트 + transcript 포인터만 남긴다.
#      session-handoff-save.sh(macOS/Linux)의 윈도우판.
#
# 동작: ~/.claude/handoffs/<레포키>/<타임스탬프>.md 로 저장(덮어쓰기 없음). 최신 10개만 보존.
# 철칙: fail-open — 어떤 오류에도 세션 종료를 막지 않는다(exit 0). 사소한 세션은 생략.

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# load와 동일한 레포 루트/키 로직 (handoff 폴더 일치 필수)
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
function Get-RepoKey([string]$root) {
    return ($root -replace '[:/\\]', '_').Trim('_')
}

# transcript(jsonl)에서 마지막 사용자 프롬프트 수집(최대 limit개, verbatim)
function Get-UserPrompts([string]$path, [int]$limit = 6) {
    $out = @()
    if ([string]::IsNullOrWhiteSpace($path) -or -not (Test-Path -LiteralPath $path)) { return $out }
    $lines = Get-Content -LiteralPath $path -Encoding utf8 -ErrorAction SilentlyContinue
    foreach ($line in $lines) {
        $line = $line.Trim()
        if (-not $line) { continue }
        $obj = $null
        try { $obj = $line | ConvertFrom-Json } catch { continue }
        if ($obj.type -ne 'user') { continue }
        $content = $obj.message.content
        $text = ''
        if ($content -is [string]) {
            $text = $content
        } elseif ($content) {
            $parts = @()
            foreach ($b in $content) {
                if ($b.type -eq 'text') { $parts += [string]$b.text }
            }
            $text = ($parts -join "`n")
        }
        $text = ($text).Trim()
        if (-not $text) { continue }
        # 시스템/훅 주입·메타 라인 제외
        if ($text.StartsWith('<') -or $text.Contains('[SYSTEM NOTIFICATION') -or $text.Contains('system-reminder')) { continue }
        $out += $text
    }
    if ($out.Count -gt $limit) { return $out[($out.Count - $limit)..($out.Count - 1)] }
    return $out
}

function Invoke-Git([string]$root, [string[]]$gitArgs) {
    try { return (& git -C $root @gitArgs 2>$null | Out-String).Trim() }
    catch { return '' }
}

function Limit-Text([string]$s, [int]$n = 400) {
    $s = $s.Trim()
    if ($s.Length -le $n) { return $s }
    return $s.Substring(0, $n) + ' …(생략)'
}

try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }

    $d = $null
    try { $d = $raw | ConvertFrom-Json } catch { exit 0 }

    $cwd = if ($d.cwd) { [string]$d.cwd } else { (Get-Location).Path }
    $transcript = if ($d.transcript_path) { [string]$d.transcript_path } else { '' }
    $reason = if ($d.reason) { [string]$d.reason } else { 'other' }
    $ts = Get-Date -Format 'yyyyMMdd-HHmmss'

    $root = Get-RepoRoot $cwd
    $key = Get-RepoKey $root

    $prompts = Get-UserPrompts $transcript
    if (-not $prompts -or $prompts.Count -eq 0) { exit 0 }   # 사용자 발화 없으면 생략

    $branch = Invoke-Git $root @('branch', '--show-current'); if (-not $branch) { $branch = '(unknown)' }
    $status = Invoke-Git $root @('status', '-s')
    $recent = Invoke-Git $root @('log', '--oneline', '-5')

    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add('# 세션 인계 메모 (자동 저장)')
    $lines.Add('')
    $lines.Add("- 저장 시각: $ts · 종료 사유: $reason")
    $lines.Add("- 레포: $root")
    $lines.Add("- 브랜치: $branch")
    $lines.Add("- transcript: $transcript")
    $lines.Add('')
    $lines.Add('## 마지막 사용자 지시(최신순 아님, 시간순)')
    foreach ($p in $prompts) { $lines.Add("- $(Limit-Text $p)") }
    $lines.Add('')
    $lines.Add('## git 상태(종료 시점)')
    $lines.Add('```')
    $lines.Add('변경:')
    $lines.Add($(if ($status) { $status } else { '(working tree clean)' }))
    $lines.Add('')
    $lines.Add('최근 커밋:')
    $lines.Add($(if ($recent) { $recent } else { '(none)' }))
    $lines.Add('```')
    $lines.Add('')
    $lines.Add('> 기계 자동 기록이다. 더 정확한 인계가 필요하면 다음 세션에서 transcript를 확인하거나, 작업 중 /handoff 로 풍부본을 남겨라.')

    $folder = Join-Path (Join-Path $HOME '.claude\handoffs') $key
    New-Item -ItemType Directory -Force -Path $folder -ErrorAction SilentlyContinue | Out-Null
    $outFile = Join-Path $folder "$ts.md"
    # UTF-8(BOM 없이) 저장 — pwsh 7의 utf8은 무BOM
    [System.IO.File]::WriteAllText($outFile, ($lines -join "`n") + "`n", (New-Object System.Text.UTF8Encoding($false)))

    # 최신 10개만 보존
    $files = Get-ChildItem -LiteralPath $folder -Filter '*.md' -File -ErrorAction SilentlyContinue | Sort-Object Name
    if ($files.Count -gt 10) {
        $files[0..($files.Count - 11)] | ForEach-Object {
            try { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue } catch { }
        }
    }
    exit 0
} catch {
    exit 0
}
