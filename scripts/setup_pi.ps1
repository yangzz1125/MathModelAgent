[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

if (-not (Get-Command pi -ErrorAction SilentlyContinue)) {
    throw "Pi is not installed or is not available on PATH."
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python is not installed or is not available on PATH."
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $repoRoot ".venv-pi"
$python = Join-Path $venv "Scripts\python.exe"
$requirements = Join-Path $repoRoot "pi\requirements.txt"

if (-not (Test-Path -LiteralPath $python)) {
    & python -m venv $venv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create $venv."
    }
}

& $python -m pip install --disable-pip-version-check -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install Pi modeling dependencies."
}

& $python -c "import adjustText, matplotlib, numpy, openpyxl, pandas, scienceplots, scipy, seaborn, sklearn; from matplotlib import font_manager; names={f.name for f in font_manager.fontManager.ttflist}; required=('Noto Serif SC','Source Han Serif SC','SimSun'); assert any(name in names for name in required), f'Missing Chinese figure font: {required}'; print('Scientific Python and figure environment: OK')"
if ($LASTEXITCODE -ne 0) {
    throw "Scientific Python or figure import/font check failed."
}

$paperCompiler = Get-Command xelatex -ErrorAction SilentlyContinue
if (-not $paperCompiler) {
    $paperCompiler = Get-Command typst -ErrorAction SilentlyContinue
}
if (-not $paperCompiler) {
    Write-Warning "Neither xelatex nor typst is available. Paper compilation will not work."
} else {
    Write-Host "Paper compiler: $($paperCompiler.Source)"
}

Write-Host "Pi: $((Get-Command pi).Source)"
Write-Host "Environment ready: $venv"
