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
$constraints = Join-Path $repoRoot "pi\constraints-win-py311.txt"
$requiredPython = "3.11"
$systemPython = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $systemPython.Trim() -ne $requiredPython) {
    throw "MathModelAgent Pi requires Python $requiredPython on Windows; found $systemPython."
}

if (-not (Test-Path -LiteralPath $python)) {
    & python -m venv $venv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create $venv."
    }
}

if (-not (Test-Path -LiteralPath $constraints)) {
    throw "Missing scientific environment lock: $constraints"
}

$venvPython = & $python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $venvPython.Trim() -ne $requiredPython) {
    throw "Existing .venv-pi uses Python $venvPython; recreate it with Python $requiredPython."
}

& $python -m pip install --disable-pip-version-check -r $requirements -c $constraints
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install Pi modeling dependencies."
}

& $python -m pip check
if ($LASTEXITCODE -ne 0) {
    throw "Scientific Python dependency consistency check failed."
}

& $python -c "from importlib.metadata import version; import adjustText, fastapi, matplotlib, multipart, numpy, openpyxl, pandas, scienceplots, scipy, seaborn, sklearn, uvicorn; expected={'numpy':'2.4.6','pandas':'3.0.5','matplotlib':'3.11.1','SciencePlots':'2.2.2','seaborn':'0.13.2','scipy':'1.17.1','scikit-learn':'1.9.0','openpyxl':'3.1.5','fastapi':'0.141.1','python-multipart':'0.0.32','uvicorn':'0.52.4','adjustText':'1.4.0'}; actual={name:version(name) for name in expected}; assert actual == expected, f'Version mismatch: {actual}'; from matplotlib import font_manager; names={f.name for f in font_manager.fontManager.ttflist}; required=('Noto Serif SC','Source Han Serif SC','SimSun'); assert any(name in names for name in required), f'Missing Chinese figure font: {required}'; print('Locked scientific Python and figure environment: OK')"
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
