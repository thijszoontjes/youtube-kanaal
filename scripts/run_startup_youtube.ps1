param(
    [string]$RepoRoot = "",
    [string]$PythonExe = "",
    [string]$BranchName = "videos-verbeteringen",
    [switch]$SkipPull,
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
Write-Host "Branch: $BranchName"
Write-Host "Command: $PythonExe $($arguments -join ' ')"
Write-Host ""

Push-Location $RepoRoot
try {
    if ($BranchName) {
        Write-Host "Switching to branch $BranchName..."
        git fetch origin $BranchName
        if ($LASTEXITCODE -ne 0) {
            throw "git fetch failed for origin/$BranchName"
        }

        git rev-parse --verify $BranchName *> $null
        if ($LASTEXITCODE -eq 0) {
            git checkout $BranchName
        } else {
            git checkout -B $BranchName "origin/$BranchName"
        }
        if ($LASTEXITCODE -ne 0) {
            throw "git checkout failed for $BranchName"
        }

        if (-not $SkipPull) {
            Write-Host "Pulling latest code for $BranchName..."
            git pull --ff-only origin $BranchName
            if ($LASTEXITCODE -ne 0) {
                throw "git pull --ff-only failed for origin/$BranchName"
            }
        }
        Write-Host ""
    }

    & $PythonExe @arguments
    $exitCode = $LASTEXITCODE
    Write-Host ""
    Write-Host "Finished with exit code $exitCode."
    exit $exitCode
}
finally {
    Pop-Location
}
