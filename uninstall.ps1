#Requires -Version 5.1
<#
.SYNOPSIS
    Uninstalls jcrefresher — stops the Task Scheduler task, removes the task,
    and deletes the virtual environment.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$taskName = "jcrefresher"
$venvParent = "$env:LOCALAPPDATA\jcrefresher"

# Stop and unregister the scheduled task
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    Write-Host "Stopping task '$taskName' ..."
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Write-Host "Unregistering task '$taskName' ..."
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Task '$taskName' removed."
} else {
    Write-Host "Task '$taskName' not found — nothing to unregister."
}

# Remove the venv directory
if (Test-Path $venvParent) {
    Write-Host "Removing $venvParent ..."
    Remove-Item -Recurse -Force $venvParent
    Write-Host "Removed $venvParent."
} else {
    Write-Host "$venvParent does not exist — nothing to remove."
}

Write-Host ""
Write-Host "jcrefresher has been uninstalled."
