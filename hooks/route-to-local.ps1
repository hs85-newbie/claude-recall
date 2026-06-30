# route-to-local.ps1 — UserPromptSubmit 훅 (Windows / pwsh 7)
#
# WHY: 요약·번역·포매팅 같은 기계적 변환은 로컬 LLM(무료)으로 위임 가능하다.
#      해당 작업으로 보이면 local-ops 에이전트 사용을 권하는 힌트를 컨텍스트에 추가한다.
#      route-to-local.sh(macOS/Linux)의 윈도우판.
#
# 입력: stdin(JSON)의 prompt 필드 / 출력: stdout(plain text → 추가 컨텍스트)
# 철칙: fail-open — 어떤 오류에도 프롬프트 제출을 막지 않는다(exit 0).

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }

    $prompt = ''
    try {
        $d = $raw | ConvertFrom-Json
        if ($d.prompt) { $prompt = [string]$d.prompt }
    } catch { exit 0 }

    $matchers = @('요약해', '번역해', '포맷팅', '포매팅', '정렬해', '변환해', '추출해')
    foreach ($kw in $matchers) {
        if ($prompt.Contains($kw)) {
            # LM Studio 가동 여부 확인 (로컬 1234 포트)
            $status = '비활성 — 클라우드 폴백'
            try {
                $r = Invoke-WebRequest -Uri 'http://localhost:1234/v1/models' `
                        -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
                if ($r.StatusCode -eq 200) { $status = '활성' }
            } catch { }   # 응답 없으면 비활성 유지

            [Console]::Out.Write(@"
[라우팅 힌트] 기계적 변환 작업으로 판단됩니다.
→ local-ops 에이전트(Gemma-4-26B, 로컬 무료) 사용을 권장합니다.
→ LM Studio 서버: $status
"@)
            exit 0
        }
    }
    exit 0
} catch {
    exit 0
}
