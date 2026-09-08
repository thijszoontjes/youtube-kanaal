param(
    [string]$RepoRoot = "",
    [string]$PythonExe = "",
    [string]$TaskName = "youtube-kanaal-startup-upload",
    [string]$BranchName = "gratis-ai-stem-chatterbox",
    [string]$ShortTimes = "10:00,13:00,15:00,19:00",
    [string]$ScheduleDate = "",
    [switch]$SkipPull,
    [switch]$SkipOllamaPull,
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

$startupScript = Join-Path $RepoRoot "scripts\run_startup_youtube.ps1"
if (-not (Test-Path $startupScript)) {
    throw "Missing startup script: $startupScript"
}

$defaultRepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$defaultPythonExe = Join-Path $defaultRepoRoot ".venv\Scripts\python.exe"
$scriptArgs = @(
    "-NoExit",
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$startupScript`""
)
if ($RepoRoot -ne $defaultRepoRoot) {
    $scriptArgs += @("-RepoRoot", "`"$RepoRoot`"")
}
if ($PythonExe -ne "python" -and $PythonExe -ne $defaultPythonExe) {
    $scriptArgs += @("-PythonExe", "`"$PythonExe`"")
}
if ($BranchName -ne "gratis-ai-stem-chatterbox") {
    $scriptArgs += @("-BranchName", "`"$BranchName`"")
}
if ($ShortTimes -ne "10:00,13:00,15:00,19:00") {
    $scriptArgs += @("-ShortTimes", "`"$ShortTimes`"")
}
if ($ScheduleDate) {
    $scriptArgs += "-ScheduleDate"
    $scriptArgs += "`"$ScheduleDate`""
}
if ($SkipPull) {
    $scriptArgs += "-SkipPull"
}
if ($SkipOllamaPull) {
    $scriptArgs += "-SkipOllamaPull"
}
if ($Upload) {
    $scriptArgs += "-Upload"
}
if ($Debug) {
    $scriptArgs += "-Debug"
}
if ($PrivacyStatus) {
    $scriptArgs += "-PrivacyStatus"
    $scriptArgs += "`"$PrivacyStatus`""
}

$action = "powershell.exe $($scriptArgs -join ' ')"
if ($action.Length -gt 261) {
    throw "The Windows Task Scheduler command is $($action.Length) characters; shorten RepoRoot or optional arguments below the 261-character limit."
}
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

schtasks /Create /F /SC ONLOGON /TN $TaskName /TR $action /RU $currentUser /IT | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "Windows Task Scheduler could not create task $TaskName."
}

Write-Host "Installed startup task: $TaskName"
Write-Host "It opens a visible PowerShell terminal at logon and runs:"
Write-Host $action
