$ErrorActionPreference = "Stop"

$실행파일 = Join-Path $PSScriptRoot "AI운영센터_실행.ps1"
if (-not (Test-Path -LiteralPath $실행파일)) {
    throw "AI운영센터_실행.ps1 파일이 없습니다."
}

$토큰 = $null
$구문오류 = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(
    $실행파일,
    [ref]$토큰,
    [ref]$구문오류
)
if ($구문오류.Count -gt 0) {
    throw "실행 스크립트 구문 오류: $($구문오류[0].Message)"
}

& $실행파일 -브라우저열지않음 -오류창열지않음
if ($LASTEXITCODE -ne 0) {
    throw "실행 스크립트가 종료 코드 $LASTEXITCODE 로 실패했습니다."
}

$상태 = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/health" -TimeoutSec 5
if ($상태.service -ne "samplebusiness-ai-company" -or $상태.status -ne "ok") {
    throw "8000 포트가 AI 운영센터 백엔드가 아닙니다."
}

$프론트주소 = 5180..5190 |
    ForEach-Object {
        try {
            $화면 = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$_/" -TimeoutSec 2
            if ($화면.Content -match 'content="samplebusiness-ai-company"') {
                "http://127.0.0.1:$_/"
            }
        }
        catch {
        }
    } |
    Select-Object -First 1

if (-not $프론트주소) {
    throw "5180번부터 5190번 사이에서 AI 운영센터 프론트엔드를 찾지 못했습니다."
}

Write-Output "AI 운영센터 실행 검증 통과: $프론트주소"
