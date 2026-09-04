[CmdletBinding()]
param(
    [ValidateSet("127.0.0.1")]
    [string]$HostAddress = "127.0.0.1",
    [int]$BridgePort = 8000,
    [int]$FrontendPort = 5173,
    [string]$Model,
    [string]$Thinking = "high"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv-pi\Scripts\python.exe"
$frontend = Join-Path $repoRoot "frontend"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Pi environment is missing. Run scripts/setup_pi.ps1 first."
}
if (-not (Get-Command pi -ErrorAction SilentlyContinue)) {
    throw "Pi is not installed or is not available on PATH."
}
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    throw "pnpm is not installed or is not available on PATH."
}

if (-not (Test-Path -LiteralPath (Join-Path $frontend "node_modules"))) {
    Push-Location $frontend
    try {
        & pnpm install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend dependency installation failed."
        }
    }
    finally {
        Pop-Location
    }
}

$env:MATHMODELAGENT_ROOT = $repoRoot
$env:VIRTUAL_ENV = Join-Path $repoRoot ".venv-pi"
$env:PATH = "$(Join-Path $repoRoot '.venv-pi\Scripts');$env:PATH"
$env:MPLBACKEND = "Agg"
$env:PYTHONUTF8 = "1"
$env:MATHMODEL_PI_THINKING = $Thinking
if ($Model) {
    $env:MATHMODEL_PI_MODEL = $Model
}

$bridgeArgs = @(
    "-m", "uvicorn", "pi.bridge:app",
    "--host", $HostAddress,
    "--port", $BridgePort.ToString()
)
$bridge = Start-Process -FilePath $python -ArgumentList $bridgeArgs -WorkingDirectory $repoRoot -PassThru -NoNewWindow

try {
    $deadline = (Get-Date).AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 250
        try {
            $null = Invoke-RestMethod "http://${HostAddress}:${BridgePort}/status" -TimeoutSec 1
            $ready = $true
        }
        catch {
            $ready = $false
        }
    } while (-not $ready -and (Get-Date) -lt $deadline -and -not $bridge.HasExited)

    if (-not $ready) {
        throw "Pi bridge did not become ready on port $BridgePort."
    }

    Write-Host "MathModelAgent Pi Web: http://${HostAddress}:${FrontendPort}/chat"
    Push-Location $frontend
    try {
        & pnpm dev --host $HostAddress --port $FrontendPort
    }
    finally {
        Pop-Location
    }
}
finally {
    if (-not $bridge.HasExited) {
        Stop-Process -Id $bridge.Id -Force
    }
}
