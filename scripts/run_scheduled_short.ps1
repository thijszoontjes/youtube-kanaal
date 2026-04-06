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

$arguments = @("-m", "youtube_kanaal", "make-short")
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

Push-Location $RepoRoot
try {
    & $PythonExe @arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
