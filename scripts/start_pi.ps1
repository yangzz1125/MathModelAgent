[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Workspace,

    [string]$Model,
    [string]$Prompt,
    [switch]$Print
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command pi -ErrorAction SilentlyContinue)) {
    throw "Pi is not installed or is not available on PATH."
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$upstreamSkills = Join-Path $repoRoot "skills"
$piSkills = Join-Path $repoRoot "pi\skills"
$entrySkill = Join-Path $piSkills "mathmodelagent-pi\SKILL.md"
$venvScripts = Join-Path $repoRoot ".venv-pi\Scripts"
$venvPython = Join-Path $venvScripts "python.exe"

foreach ($requiredPath in @($upstreamSkills, $piSkills, $entrySkill, $venvPython)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Missing MathModelAgent Pi integration path: $requiredPath"
    }
}

New-Item -ItemType Directory -Force -Path $Workspace | Out-Null
$workspacePath = (Resolve-Path -LiteralPath $Workspace).Path
$env:MATHMODELAGENT_ROOT = $repoRoot
$env:VIRTUAL_ENV = Join-Path $repoRoot ".venv-pi"
$env:PATH = "$venvScripts;$env:PATH"
$env:MPLBACKEND = "Agg"
$env:PYTHONUTF8 = "1"

$piArgs = @(
    "--skill", $upstreamSkills,
    "--skill", $piSkills
)

if ($Model) {
    $piArgs += @("--model", $Model)
}

if ($Print) {
    if (-not $Prompt) {
        throw "-Print requires -Prompt."
    }
    $piArgs += @("--append-system-prompt", $entrySkill, "--print")
}

if ($Prompt) {
    $piArgs += @("--", $Prompt)
}

Push-Location $workspacePath
try {
    if (-not $Prompt) {
        Write-Host "MathModelAgent skills loaded from: $upstreamSkills"
        Write-Host "Project workspace: $workspacePath"
        Write-Host "Start with: /skill:mathmodelagent-pi"
    }
    & pi @piArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Pi exited with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
