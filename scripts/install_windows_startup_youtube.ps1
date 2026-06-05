param(
    [string]$RepoRoot = "",
    [string]$PythonExe = "",
    [string]$TaskName = "youtube-kanaal-startup-upload",
    [string]$BranchName = "videos-verbeteringen",
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

$scriptArgs = @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$startupScript`"",
    "-RepoRoot", "`"$RepoRoot`"",
    "-PythonExe", "`"$PythonExe`"",
    "-BranchName", "`"$BranchName`"",
    "-ShortTimes", "`"$ShortTimes`""
)
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
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

schtasks /Create /F /SC ONLOGON /TN $TaskName /TR $action /RU $currentUser /IT | Out-Host

Write-Host "Installed startup task: $TaskName"
Write-Host "It opens a visible PowerShell terminal at logon and runs:"
Write-Host $action
