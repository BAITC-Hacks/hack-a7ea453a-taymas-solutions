param(
    [switch]$CheckRepro,
    [switch]$NoCopilot,
    [switch]$SkipPipInstall,
    [switch]$SkipNpmInstall,
    [int]$FrontendPort = 5173,
    [int]$CopilotPort = 8765
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DataDir = Join-Path $Root "data"
$OutDir = Join-Path $Root "out"
$FrontendDir = Join-Path $Root "frontend"

function Test-PythonCandidate {
    param([string]$Exe, [string[]]$PrefixArgs = @())
    try {
        & $Exe @PrefixArgs -c "import sys; print(sys.version)" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

$PythonExe = $null
$PythonPrefix = @()
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$UseVenv = $false
if ((Test-Path $VenvPython) -and (Test-PythonCandidate $VenvPython)) {
    $PythonExe = $VenvPython
    $UseVenv = $true
} elseif (Test-PythonCandidate "python") {
    $PythonExe = "python"
} elseif (Test-PythonCandidate "python3") {
    $PythonExe = "python3"
} elseif (Test-PythonCandidate "py" @("-3")) {
    $PythonExe = "py"
    $PythonPrefix = @("-3")
} else {
    throw "Python 3.11+ не найден. Установите Python и зависимости: pip install -r requirements.txt"
}

if (-not $UseVenv) {
    Write-Host "==> Creating local Python environment"
    & $PythonExe @PythonPrefix -m venv (Join-Path $Root ".venv")
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    $PythonExe = $VenvPython
    $PythonPrefix = @()
}

foreach ($Name in @("nodes.parquet", "edges.parquet", "transactions.parquet")) {
    $Path = Join-Path $DataDir $Name
    if (-not (Test-Path $Path)) {
        throw "Не найден $Path. Распакуйте датасет организаторов в папку data/."
    }
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Push-Location $Root
try {
    if (-not $SkipPipInstall) {
        & $PythonExe @PythonPrefix -c "import pandas, pyarrow, networkx, numpy, scipy" *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "==> Installing Python dependencies"
            & $PythonExe @PythonPrefix -m pip install -r requirements.txt
            if ($LASTEXITCODE -ne 0) {
                exit $LASTEXITCODE
            }
        }
    }

    $PipelineArgs = @("-m", "money_graph", "--data", "data", "--out", "out")
    if ($CheckRepro) {
        $PipelineArgs += "--check-repro"
    }
    Write-Host "==> Running pipeline"
    & $PythonExe @PythonPrefix @PipelineArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    $Copilot = $null
    if (-not $NoCopilot) {
        Write-Host "==> Starting Copilot API on http://127.0.0.1:$CopilotPort"
        $CopilotArgs = @()
        $CopilotArgs += $PythonPrefix
        $CopilotArgs += @("-m", "agent_orchestrator.server", "--out", "out", "--port", "$CopilotPort")
        $Copilot = Start-Process -FilePath $PythonExe -ArgumentList $CopilotArgs -WorkingDirectory $Root -PassThru -WindowStyle Hidden
        Start-Sleep -Seconds 2
        if ($Copilot.HasExited) {
            throw "Copilot API не стартовал. Проверьте out/*.csv и свободен ли порт $CopilotPort."
        }
    }

    try {
        Push-Location $FrontendDir
        if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
            throw "npm не найден. Установите Node.js 20.19+ или 22.12+."
        }
        if ((-not $SkipNpmInstall) -and (-not (Test-Path "node_modules"))) {
            Write-Host "==> Installing frontend dependencies"
            npm ci
            if ($LASTEXITCODE -ne 0) {
                exit $LASTEXITCODE
            }
        }
        Write-Host "==> Starting UI on http://127.0.0.1:$FrontendPort"
        Write-Host "Press Ctrl+C to stop."
        npm run dev -- --host 127.0.0.1 --port $FrontendPort --open
        exit $LASTEXITCODE
    } finally {
        Pop-Location
        if ($Copilot -and -not $Copilot.HasExited) {
            Stop-Process -Id $Copilot.Id -Force
        }
    }
} finally {
    Pop-Location
}
