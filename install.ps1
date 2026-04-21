#Requires -Version 5.1
<#
.SYNOPSIS
    Installs jcrefresher as a Windows Task Scheduler task that runs at logon.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# 1. Check Python 3
# ---------------------------------------------------------------------------
Write-Host "Checking for Python 3..."
$pythonOk = $false
try {
    $ver = & python --version 2>&1
    if ($ver -match "Python 3") { $pythonOk = $true }
} catch { }

if (-not $pythonOk) {
    Write-Host "Python 3 not found. Attempting to install via winget..."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Error "winget is not available on this machine. Please install Python 3 manually from https://python.org and re-run this script."
        exit 1
    }
    winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    # Refresh PATH for this session
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")
}

# ---------------------------------------------------------------------------
# 2. Check uvx / uv
# ---------------------------------------------------------------------------
Write-Host "Checking for uvx..."
if (-not (Get-Command uvx -ErrorAction SilentlyContinue)) {
    Write-Host "uvx not found. Installing uv via winget..."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Error "winget is not available on this machine. Please install uv manually from https://github.com/astral-sh/uv and re-run this script."
        exit 1
    }
    winget install --id astral-sh.uv --silent --accept-package-agreements --accept-source-agreements
    # Add uv's default install location to PATH for this session
    $uvBin = "$env:USERPROFILE\.local\bin"
    if ($env:PATH -notlike "*$uvBin*") {
        $env:PATH = "$uvBin;$env:PATH"
    }
}

# ---------------------------------------------------------------------------
# 3. Detect .code-index location
# ---------------------------------------------------------------------------
Write-Host "Detecting .code-index location..."

$diagnosticSnippet = @"
Please find where jcodemunch stores its index database on this machine.
Check these locations and tell me which one contains .db files:
- `$HOME\.code-index\
- `$env:LOCALAPPDATA\.code-index\
- `$env:APPDATA\.code-index\
Run: dir `$HOME\.code-index, dir `$env:LOCALAPPDATA\.code-index, dir `$env:APPDATA\.code-index
Then tell me the full path of the folder that contains .db files.
"@

$codeIndexPath = $null
$candidates = @(
    "$HOME\.code-index",
    "$env:LOCALAPPDATA\.code-index",
    "$env:APPDATA\.code-index"
)

foreach ($candidate in $candidates) {
    if (Test-Path $candidate) {
        $codeIndexPath = $candidate
        Write-Host "Found .code-index at: $codeIndexPath"
        break
    }
}

if ($null -eq $codeIndexPath) {
    Write-Host ""
    Write-Host "Could not automatically find a .code-index folder on this machine."
    Write-Host "jcodemunch may not have been run yet, or it stores its index somewhere unexpected."
    Write-Host ""
    Write-Host "To locate it, give the following diagnostic snippet to Claude Code:"
    Write-Host ""
    Write-Host $diagnosticSnippet
    Write-Host ""
    $userPath = Read-Host "Paste the full path to the .code-index folder here"
    if (-not (Test-Path $userPath)) {
        Write-Error "The path '$userPath' does not exist. Please verify and re-run the script."
        exit 1
    }
    $codeIndexPath = $userPath
    Write-Host "Using .code-index path: $codeIndexPath"
}

# ---------------------------------------------------------------------------
# 4. Create venv and install watchdog
# ---------------------------------------------------------------------------
$venvDir = "$env:LOCALAPPDATA\jcrefresher\venv"
Write-Host "Creating virtual environment at $venvDir ..."
python -m venv $venvDir
& "$venvDir\Scripts\pip.exe" install --quiet watchdog

# ---------------------------------------------------------------------------
# 5. Register Task Scheduler task
# ---------------------------------------------------------------------------
$taskName = "jcrefresher"
$pythonExe = "$venvDir\Scripts\python.exe"
$scriptRoot = $PSScriptRoot

Write-Host "Registering Task Scheduler task '$taskName' ..."

# Remove existing task if present
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    Write-Host "Existing task found — removing it before re-registering."
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

$action = New-ScheduledTaskAction `
    -Execute $pythonExe `
    -Argument "-m jcrefresher" `
    -WorkingDirectory $scriptRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

$envVar = [Microsoft.Win32.Registry]::GetValue(
    "HKEY_CURRENT_USER\Environment", "PYTHONPATH", $null)
$newPythonPath = if ($envVar) { "$scriptRoot;$envVar" } else { $scriptRoot }

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

# Build the task with an environment variable for PYTHONPATH via XML workaround
# because New-ScheduledTaskAction does not support env vars directly.
Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "jcrefresher — watches jcodemunch index and re-indexes changed repos"

# Inject PYTHONPATH via the task XML
$xml = (Export-ScheduledTask -TaskName $taskName)
$envXml = "<EnvironmentVariables><Variable><Name>PYTHONPATH</Name><Value>$scriptRoot</Value></Variable></EnvironmentVariables>"
# Insert before closing </Exec> tag
$xml = $xml -replace "</Exec>", "$envXml</Exec>"
# Re-register with updated XML
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
Register-ScheduledTask -TaskName $taskName -Xml $xml -Force | Out-Null

Write-Host "Task '$taskName' registered."

# ---------------------------------------------------------------------------
# 6. Start the task immediately
# ---------------------------------------------------------------------------
Write-Host "Starting task '$taskName' ..."
Start-ScheduledTask -TaskName $taskName

# ---------------------------------------------------------------------------
# 7. Print success
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "jcrefresher is now installed and running."
Write-Host ""
Write-Host "To check the task status:"
Write-Host "    Get-ScheduledTask -TaskName jcrefresher"
Write-Host ""
Write-Host "To see recent runs:"
Write-Host "    Get-ScheduledTaskInfo -TaskName jcrefresher"
Write-Host ""
Write-Host "Logs: open Event Viewer > Windows Logs > Application and filter by source 'jcrefresher',"
Write-Host "      or check stderr output via Task Scheduler's History tab."
