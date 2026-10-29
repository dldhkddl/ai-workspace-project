param(
    [ValidateRange(1024, 65525)]
    [int]$기본프론트포트 = 5180,
    [switch]$브라우저열지않음,
    [switch]$오류창열지않음
)

$ErrorActionPreference = "Stop"
$프로젝트루트 = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$백엔드루트 = Join-Path $프로젝트루트 "backend"
$백엔드주소 = "http://127.0.0.1:8000/api/v1/health"
$데이터베이스포트 = 54322
$도커구성파일 = Join-Path $프로젝트루트 "docker-compose.yml"
$프론트포트 = $null
$프론트주소 = $null

function Show-실행오류 {
    param([string]$메시지)

    if ($오류창열지않음) {
        Write-Error $메시지
        return
    }

    Add-Type -AssemblyName System.Windows.Forms
    [void][System.Windows.Forms.MessageBox]::Show(
        $메시지,
        "AI 운영센터 실행 오류",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    )
}

function Test-포트열림 {
    param([int]$포트)

    $연결 = [System.Net.Sockets.TcpClient]::new()
    try {
        $대기 = $연결.BeginConnect("127.0.0.1", $포트, $null, $null)
        if (-not $대기.AsyncWaitHandle.WaitOne(300)) {
            return $false
        }
        $연결.EndConnect($대기)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $연결.Dispose()
    }
}

function Test-백엔드정상 {
    try {
        $응답 = Invoke-RestMethod -Uri $백엔드주소 -TimeoutSec 2
        return $응답.service -eq "samplebusiness-ai-company" -and $응답.status -eq "ok"
    }
    catch {
        return $false
    }
}

function Find-도커실행파일 {
    $명령 = Get-Command "docker.exe" -ErrorAction SilentlyContinue
    if ($명령) {
        return $명령.Source
    }

    $후보 = @(
        (Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin\docker.exe"),
        (Join-Path $env:ProgramFiles "Docker\Docker\resources\bin\docker.exe")
    )
    return $후보 | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}

function Find-도커데스크톱 {
    $후보 = @(
        (Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\Docker Desktop.exe"),
        (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe")
    )
    return $후보 | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}

function Test-도커정상 {
    param([string]$도커)

    $확인프로세스 = Start-Process -FilePath $도커 `
        -ArgumentList @("info") `
        -WindowStyle Hidden `
        -PassThru
    if (-not $확인프로세스.WaitForExit(5000)) {
        Stop-Process -Id $확인프로세스.Id -Force -ErrorAction SilentlyContinue
        return $false
    }
    return $확인프로세스.ExitCode -eq 0
}

function Start-데이터베이스 {
    if (Test-포트열림 $데이터베이스포트) {
        return
    }

    $도커 = Find-도커실행파일
    if (-not $도커) {
        throw "Docker 실행 파일을 찾지 못했습니다. Docker Desktop 설치 상태를 확인해 주세요."
    }

    if (-not (Test-도커정상 $도커)) {
        $도커데스크톱 = Find-도커데스크톱
        if (-not $도커데스크톱) {
            throw "Docker Desktop을 찾지 못했습니다."
        }
        Start-Process -FilePath $도커데스크톱 -WindowStyle Hidden
        if (-not (Wait-준비 { Test-도커정상 $도커 } 90)) {
            throw "Docker Desktop이 90초 안에 준비되지 않았습니다."
        }
    }

    & $도커 compose -f $도커구성파일 up -d postgres
    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL 컨테이너를 시작하지 못했습니다."
    }
    if (-not (Wait-준비 { Test-포트열림 $데이터베이스포트 } 60)) {
        throw "PostgreSQL이 60초 안에 준비되지 않았습니다."
    }
}

function Test-프론트정상 {
    param([int]$포트)

    try {
        $응답 = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$포트/" -TimeoutSec 2
        return $응답.Content -match 'content="samplebusiness-ai-company"'
    }
    catch {
        return $false
    }
}

function Wait-준비 {
    param(
        [scriptblock]$검사,
        [int]$제한초 = 30
    )

    $마감 = [DateTime]::UtcNow.AddSeconds($제한초)
    while ([DateTime]::UtcNow -lt $마감) {
        if (& $검사) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Find-백엔드파이썬 {
    $후보 = @(
        (Join-Path $백엔드루트 ".venv\Scripts\python.exe"),
        (Join-Path $백엔드루트 ".venv-test\Scripts\python.exe")
    )

    foreach ($실행파일 in $후보) {
        if (-not (Test-Path -LiteralPath $실행파일)) {
            continue
        }
        try {
            & $실행파일 -c "import uvicorn" 2>$null
            if ($LASTEXITCODE -eq 0) {
                return $실행파일
            }
        }
        catch {
            continue
        }
    }
    return $null
}

try {
    if (-not (Test-Path -LiteralPath (Join-Path $프로젝트루트 "package.json"))) {
        throw "AI-company 저장소를 찾지 못했습니다: $프로젝트루트"
    }

    if (-not (Test-백엔드정상)) {
        if (Test-포트열림 8000) {
            throw "8000 포트를 다른 프로그램이 사용 중입니다. 해당 프로그램을 확인한 뒤 다시 실행해 주세요."
        }

        $파이썬 = Find-백엔드파이썬
        if (-not $파이썬) {
            throw "사용 가능한 백엔드 Python 환경을 찾지 못했습니다. backend/.venv-test를 준비해 주세요."
        }

        Start-데이터베이스

        & $파이썬 -m alembic -c (Join-Path $백엔드루트 "alembic.ini") upgrade head
        if ($LASTEXITCODE -ne 0) {
            throw "데이터베이스 업데이트에 실패했습니다. 백엔드 설정과 PostgreSQL 상태를 확인해 주세요."
        }

        Start-Process -FilePath $파이썬 `
            -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
            -WorkingDirectory $백엔드루트 `
            -WindowStyle Hidden

        Start-Process -FilePath $파이썬 `
            -ArgumentList @("-m", "app.worker") `
            -WorkingDirectory $백엔드루트 `
            -WindowStyle Hidden
    }

    $프론트포트 = $기본프론트포트..($기본프론트포트 + 10) |
        Where-Object { Test-프론트정상 $_ } |
        Select-Object -First 1

    if (-not $프론트포트) {
        $프론트포트 = $기본프론트포트..($기본프론트포트 + 10) |
            Where-Object { -not (Test-포트열림 $_) } |
            Select-Object -First 1
        if (-not $프론트포트) {
            throw "$기본프론트포트 번부터 $($기본프론트포트 + 10) 번까지 사용할 수 있는 포트가 없습니다."
        }

        $npm = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
        if (-not $npm) {
            throw "npm.cmd를 찾지 못했습니다. Node.js 설치 상태를 확인해 주세요."
        }

        Start-Process -FilePath $env:ComSpec `
            -ArgumentList @("/d", "/c", "npm.cmd", "run", "dev", "--", "--host", "127.0.0.1", "--port", "$프론트포트", "--strictPort") `
            -WorkingDirectory $프로젝트루트 `
            -WindowStyle Hidden
    }

    $프론트주소 = "http://127.0.0.1:$프론트포트/"

    if (-not (Wait-준비 { Test-백엔드정상 })) {
        throw "백엔드가 30초 안에 준비되지 않았습니다."
    }
    if (-not (Wait-준비 { Test-프론트정상 $프론트포트 })) {
        throw "프론트엔드가 30초 안에 준비되지 않았습니다."
    }

    if (-not $브라우저열지않음) {
        Start-Process $프론트주소
    }
    exit 0
}
catch {
    Show-실행오류 $_.Exception.Message
    exit 1
}
