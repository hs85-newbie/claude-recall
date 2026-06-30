# git-safety-guard.ps1 — PreToolUse(Bash) 훅 (Windows / pwsh 7)
#
# WHY: AI 에이전트가 일으킨 가장 위험한 사고가 git 파괴적 연산이었다.
#      되돌리기 어려운 git 명령을 실행 직전 가로채 사용자 확인을 요구한다.
#      git-safety-guard.sh(macOS/Linux)의 동작을 그대로 옮긴 윈도우판.
#
# 동작: stdin(JSON)의 tool_input.command에서 위험 패턴을 찾으면
#       permissionDecision=ask 를 출력해 사용자 확인을 띄운다. 그 외엔 통과.
# 철칙: fail-open — 파싱/판정 중 어떤 오류가 나도 작업을 막지 않는다(exit 0).

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Pass { exit 0 }   # 통과(아무 출력 없으면 기본 권한 흐름)

function Ask([string]$reason) {
    # $reason = 사용자에게 보일 사유
    $payload = @{
        hookSpecificOutput = @{
            hookEventName          = 'PreToolUse'
            permissionDecision     = 'ask'
            permissionDecisionReason = $reason
        }
    }
    # -Compress: 한 줄 JSON. -Depth: 중첩 보존.
    [Console]::Out.Write(($payload | ConvertTo-Json -Compress -Depth 5))
    exit 0
}

try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { Pass }

    $cmd = ''
    try {
        $d = $raw | ConvertFrom-Json
        if ($d.tool_input -and $d.tool_input.command) { $cmd = [string]$d.tool_input.command }
    } catch { Pass }   # 파싱 실패 시 통과

    if ([string]::IsNullOrWhiteSpace($cmd)) { Pass }
    if ($cmd -notmatch 'git') { Pass }   # git 명령 아니면 즉시 통과

    # .NET 정규식은 기본 대소문자 구분 안 함 → grep -Ei 와 동일.
    if ($cmd -match 'git\s+([^&|;]*\s)?reset\s+([^&|;]*\s)?--hard') {
        Ask '⚠️ git reset --hard 감지 — 현재 브랜치의 커밋·작업이 영구 소실될 수 있습니다. 진행 전 백업 브랜치(git branch backup-<현재시각>)를 만들었는지 확인하세요. 정말 실행할까요?'
    }

    # force push: --force 또는 -f. 단 --force-with-lease(안전)는 제외.
    if (($cmd -match 'git\s+([^&|;]*\s)?push') -and
        ($cmd -match '(--force([^-]|$)|\s-f(\s|$))') -and
        ($cmd -notmatch '--force-with-lease')) {
        Ask '⚠️ git push --force 감지 — 원격 히스토리를 덮어써 동료/CI의 커밋을 날릴 수 있습니다. --force-with-lease가 더 안전합니다. 정말 강제 push할까요?'
    }

    if ($cmd -match 'git\s+([^&|;]*\s)?clean\s+-[a-z]*f') {
        Ask '⚠️ git clean -f 감지 — 추적되지 않은 파일이 영구 삭제됩니다(휴지통 없음). 먼저 git clean -n으로 대상을 확인했나요? 실행할까요?'
    }

    if ($cmd -match 'git\s+([^&|;]*\s)?branch\s+([^&|;]*\s)?-D') {
        Ask '⚠️ git branch -D 감지 — 병합되지 않은 브랜치를 강제 삭제합니다. 작업이 유실될 수 있습니다. 실행할까요?'
    }

    if ($cmd -match 'git\s+([^&|;]*\s)?(checkout|switch)\s+([^&|;]*\s)?(-f|--force)') {
        Ask '⚠️ git checkout/switch --force 감지 — 작업 트리의 미저장 변경이 버려집니다. 실행할까요?'
    }

    # 작업 트리 변경 폐기: `git checkout .` / `git checkout -- <path>`
    if ($cmd -match 'git\s+checkout\s+([^&|;]*\s)?(--\s|\.([\s/]|$))') {
        Ask '⚠️ git checkout으로 작업 트리 변경 폐기 감지 — 저장 안 한 수정이 사라집니다. 실행할까요?'
    }
    if (($cmd -match 'git\s+restore(\s|$)') -and ($cmd -notmatch '--staged')) {
        Ask '⚠️ git restore 감지 — 작업 트리의 미저장 변경이 폐기됩니다. 실행할까요?'
    }

    Pass
} catch {
    # 어떤 예외에도 작업을 막지 않는다.
    exit 0
}
