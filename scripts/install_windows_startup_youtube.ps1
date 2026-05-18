param(
    [string]$RepoRoot = "",
    [string]$PythonExe = "",
    [string]$TaskName = "youtube-kanaal-startup-upload",
    [string]$PublishFor = "today",
    [string]$ShortTimes = "10:00,13:00,15:00,19:00",
    [string]$VideoTime = "17:00",
    [string]$OllamaModel = "llama3.2:3b",
    [int]$DelayMinutes = 2,
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

$runnerArguments = @(
    "-NoExit",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    "`"$runnerScript`"",
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
    $runnerArguments += "-SkipOllamaPull"
}
if ($DryRun) {
    $runnerArguments += "-DryRun"
}
if ($Debug) {
    $runnerArguments += "-Debug"
}

$taskAction = "powershell.exe $($runnerArguments -join ' ')"
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
if ($DelayMinutes -gt 0) {
    Write-Host "Delay: $DelayMinutes minute(s) after login"
}
Write-Host ""

$createArguments = $createCommand[1..($createCommand.Count - 1)]
& $createCommand[0] @createArguments
if ($LASTEXITCODE -ne 0) {
    throw "schtasks failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Installed. The task will run when you log in to Windows."
Write-Host "Test now with:"
Write-Host "schtasks /Run /TN `"$TaskName`""
Write-Host ""
Write-Host "Remove later with:"
Write-Host "schtasks /Delete /TN `"$TaskName`" /F"
