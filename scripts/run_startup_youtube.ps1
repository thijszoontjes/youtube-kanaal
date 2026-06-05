param(
    [string]$RepoRoot = "",
    [string]$PythonExe = "",
    [string]$BranchName = "videos-verbeteringen",
    [switch]$SkipPull,
    [switch]$SkipOllamaPull,
    [switch]$Upload,
    [switch]$Debug,
    [string]$PrivacyStatus = ""
)

$ErrorActionPreference = "Stop"

function Get-DotEnvValue {
    param(
        [string]$Path,
        [string]$Name,
        [string]$Default = ""
    )

    if (-not (Test-Path $Path)) {
        return $Default
    }

    foreach ($line in Get-Content $Path) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.*)\s*$") {
            $value = $Matches[1].Trim()
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            return $value
        }
    }

    return $Default
}

function Test-OllamaApi {
    param([string]$BaseUrl)

    try {
        $tagsUrl = "$($BaseUrl.TrimEnd('/'))/api/tags"
        Invoke-WebRequest -Uri $tagsUrl -UseBasicParsing -TimeoutSec 5 *> $null
        return $true
    } catch {
        return $false
    }
}

function Wait-OllamaApi {
    param(
        [string]$BaseUrl,
        [int]$TimeoutSeconds = 90
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-OllamaApi -BaseUrl $BaseUrl) {
            return $true
        }
        Start-Sleep -Seconds 2
    }

    return $false
}

function Resolve-OllamaExe {
    $command = Get-Command "ollama" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $localAppData = [Environment]::GetFolderPath("LocalApplicationData")
    $candidates = @(
        (Join-Path $localAppData "Programs\Ollama\ollama.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe")
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    return ""
}

function Ensure-OllamaReady {
    param(
        [string]$RepoRoot,
        [switch]$SkipModelPull
    )

    $envPath = Join-Path $RepoRoot ".env"
    $baseUrl = Get-DotEnvValue -Path $envPath -Name "OLLAMA_BASE_URL" -Default "http://127.0.0.1:11434"
    $model = Get-DotEnvValue -Path $envPath -Name "OLLAMA_MODEL" -Default "llama3.1:8b-instruct"

    Write-Host "Ollama: $baseUrl"
    Write-Host "Ollama model: $model"

    $ollamaExe = Resolve-OllamaExe
    if (-not $ollamaExe) {
        throw "Ollama command was not found. Install Ollama or add it to PATH."
    }

    if (-not (Test-OllamaApi -BaseUrl $baseUrl)) {
        Write-Host "Starting Ollama server..."
        Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden
    }

    Write-Host "Waiting for Ollama API..."
    if (-not (Wait-OllamaApi -BaseUrl $baseUrl -TimeoutSeconds 90)) {
        throw "Ollama API did not become reachable at $baseUrl."
    }

    if (-not $SkipModelPull) {
        Write-Host "Checking Ollama model..."
        $models = (& $ollamaExe list 2>$null) -join "`n"
        if ($LASTEXITCODE -ne 0 -or $models -notmatch "(?m)^\s*$([regex]::Escape($model))\s+") {
            Write-Host "Pulling Ollama model $model..."
            & $ollamaExe pull $model
            if ($LASTEXITCODE -ne 0) {
                throw "ollama pull failed for $model"
            }
        } else {
            Write-Host "Ollama model already available."
        }
    }

    Write-Host ""
}

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

    Ensure-OllamaReady -RepoRoot $RepoRoot -SkipModelPull:$SkipOllamaPull

    & $PythonExe @arguments
    $exitCode = $LASTEXITCODE
    Write-Host ""
    Write-Host "Finished with exit code $exitCode."
    exit $exitCode
}
finally {
    Pop-Location
}
