param(
    [string]$RepoRoot = "",
    [string]$PythonExe = "",
    [string]$PublishFor = "today",
    [string]$ShortTimes = "10:00,13:00,15:00,19:00",
    [string]$VideoTime = "17:00",
    [string]$OllamaModel = "llama3.2:3b",
    [switch]$SkipOllamaPull,
    [switch]$DryRun,
    [switch]$Debug
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

$env:LONG_PUBLISH_TIME = $VideoTime

Write-Host ""
Write-Host "youtube-kanaal startup upload"
Write-Host "Repo: $RepoRoot"
Write-Host "Python: $PythonExe"
Write-Host "Publish day: $PublishFor"
Write-Host "Short times: $ShortTimes"
Write-Host "Long video time: $VideoTime"
Write-Host ""

Push-Location $RepoRoot
try {
    if (-not $SkipOllamaPull) {
        Write-Host "Checking Ollama model: $OllamaModel"
        & ollama pull $OllamaModel
        if ($LASTEXITCODE -ne 0) {
            throw "ollama pull failed with exit code $LASTEXITCODE"
        }
    }

    $arguments = @(
        "-m",
        "youtube_kanaal",
        "daily-content",
        "--for",
        $PublishFor,
        "--short-times",
        $ShortTimes,
        "--video-time",
        $VideoTime
    )
    if ($DryRun) {
        $arguments += "--dry-run"
    }
    if ($Debug) {
        $arguments += "--debug"
    }

    Write-Host ""
    Write-Host "Running: $PythonExe $($arguments -join ' ')"
    Write-Host ""
    & $PythonExe @arguments
    $exitCode = $LASTEXITCODE

    Write-Host ""
    if ($exitCode -eq 0) {
        Write-Host "Done."
    } else {
        Write-Host "Command failed with exit code $exitCode."
    }
}
catch {
    Write-Host ""
    Write-Host "Startup upload failed:"
    Write-Host $_
}
finally {
    Pop-Location
}
