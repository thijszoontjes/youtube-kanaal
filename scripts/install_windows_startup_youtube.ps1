param(
    [string]$RepoRoot = "",
    [string]$PythonExe = "",
    [string]$TaskName = "youtube-kanaal-startup-upload",
    [string]$PublishFor = "tomorrow",
    [string]$ShortTimes = "10:00,13:00,15:00,19:00",
    [string]$VideoTime = "17:00",
    [string]$OllamaModel = "llama3.2:3b",
    [int]$DelayMinutes = 2,
    [switch]$NoInstagramReels,
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

$runnerScript = Join-Path $RepoRoot "scripts\run_startup_youtube.ps1"
if (-not (Test-Path $runnerScript)) {
    throw "Missing startup runner script: $runnerScript"
}

if (-not $PythonExe) {
    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        $PythonExe = $venvPython
    } else {
        $PythonExe = "python"
    }
}

$scriptArguments = @(
    "-RepoRoot",
    "`"$RepoRoot`"",
    "-PythonExe",
    "`"$PythonExe`"",
    "-PublishFor",
    "`"$PublishFor`"",
    "-ShortTimes",
    "`"$ShortTimes`"",
    "-VideoTime",
    "`"$VideoTime`"",
    "-OllamaModel",
    "`"$OllamaModel`""
)
if ($SkipOllamaPull) {
    $scriptArguments += "-SkipOllamaPull"
}
if ($NoInstagramReels) {
    $scriptArguments += "-NoInstagramReels"
}
if ($DryRun) {
    $scriptArguments += "-DryRun"
}
if ($Debug) {
    $scriptArguments += "-Debug"
}

$launcherDir = Join-Path $env:LOCALAPPDATA "youtube-kanaal"
if (-not (Test-Path $launcherDir)) {
    New-Item -ItemType Directory -Path $launcherDir | Out-Null
}
$launcherPath = Join-Path $launcherDir "startup-youtube.ps1"
$launcherCommand = "& `"$runnerScript`" $($scriptArguments -join ' ')"
Set-Content -Path $launcherPath -Value @('$ErrorActionPreference = "Stop"', $launcherCommand) -Encoding ASCII

$taskAction = "powershell.exe -NoExit -ExecutionPolicy Bypass -File `"$launcherPath`""
$createCommand = @(
    "schtasks",
    "/Create",
    "/SC",
    "ONLOGON",
    "/TN",
    $TaskName,
    "/TR",
    $taskAction,
    "/F"
)

if ($DelayMinutes -gt 0) {
    $delayValue = "{0:D4}:00" -f $DelayMinutes
    $createCommand += @("/DELAY", $delayValue)
}

Write-Host "Installing Windows startup task:"
Write-Host "Task: $TaskName"
Write-Host "Action: $taskAction"
Write-Host "Launcher: $launcherPath"
Write-Host "YouTube publish day: $PublishFor"
Write-Host "Instagram Reels: $(if ($NoInstagramReels) { 'no' } else { 'today/immediate' })"
if ($DelayMinutes -gt 0) {
    Write-Host "Delay: $DelayMinutes minute(s) after login"
}
Write-Host ""

$createArguments = $createCommand[1..($createCommand.Count - 1)]
& $createCommand[0] @createArguments
if ($LASTEXITCODE -ne 0) {
    $taskExitCode = $LASTEXITCODE
    $startupFolder = [Environment]::GetFolderPath("Startup")
    if (-not $startupFolder) {
        throw "schtasks failed with exit code $taskExitCode and the Windows Startup folder could not be resolved."
    }
    $startupShortcut = Join-Path $startupFolder "$TaskName.cmd"
    $startupCommand = "start `"youtube-kanaal startup`" powershell.exe -NoExit -ExecutionPolicy Bypass -File `"$launcherPath`""
    $startupLines = @("@echo off")
    if ($DelayMinutes -gt 0) {
        $delaySeconds = $DelayMinutes * 60
        $startupLines += "timeout /t $delaySeconds /nobreak >nul"
    }
    $startupLines += $startupCommand
    Set-Content -Path $startupShortcut -Value $startupLines -Encoding ASCII

    Write-Host ""
    Write-Host "Task Scheduler was not available, so a Startup folder launcher was installed instead."
    Write-Host "Startup launcher: $startupShortcut"
    Write-Host "It will run when you log in to Windows."
    Write-Host ""
    Write-Host "Test now with:"
    Write-Host "`"$startupShortcut`""
    Write-Host ""
    Write-Host "Remove later by deleting:"
    Write-Host $startupShortcut
    return
}

Write-Host ""
Write-Host "Installed. The task will run when you log in to Windows."
Write-Host "Test now with:"
Write-Host "schtasks /Run /TN `"$TaskName`""
Write-Host ""
Write-Host "Remove later with:"
Write-Host "schtasks /Delete /TN `"$TaskName`" /F"
