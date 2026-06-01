param(
    [string]$RepoRoot = "",
    [string]$PythonExe = "",
    [switch]$Upload,
    [switch]$Debug,
    [string]$PrivacyStatus = ""
)

$ErrorActionPreference = "Stop"

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $RepoRoot = (Resolve-Path $RepoRoot).Path
}

if (-not $PythonExe) {
    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        $PythonExe = $venvPython
    } else {
        $PythonExe = "python"
    }
}

$arguments = @("-m", "youtube_kanaal", "scheduled-run")
if ($Upload) {
    $arguments += "--upload"
}
if ($Debug) {
    $arguments += "--debug"
}
if ($PrivacyStatus) {
    $arguments += "--privacy-status"
    $arguments += $PrivacyStatus
}

Write-Host "youtube-kanaal startup run"
Write-Host "Repo: $RepoRoot"
Write-Host "Python: $PythonExe"
Write-Host "Command: $PythonExe $($arguments -join ' ')"
Write-Host ""

Push-Location $RepoRoot
try {
    & $PythonExe @arguments
    $exitCode = $LASTEXITCODE
    Write-Host ""
    Write-Host "Finished with exit code $exitCode."
    exit $exitCode
}
finally {
    Pop-Location
}
