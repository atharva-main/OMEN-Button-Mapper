<#
.SYNOPSIS
    Uninstalls OmenButtonListener, stops its background process, and removes the scheduled task.
.DESCRIPTION
    1. Stops any active listener process.
    2. Removes the 'OmenButtonRemapperListener' scheduled task from Windows Task Scheduler.
    3. Removes installed binaries from %LOCALAPPDATA%\OmenButtonRemapper\bin.
    4. Preserves the user's configuration and logs by default, unless -RemoveConfig is specified.
.PARAMETER RemoveConfig
    If specified, deletes the entire %LOCALAPPDATA%\OmenButtonRemapper directory including config.json and logs.
#>
[CmdletBinding()]
param(
    [switch]$RemoveConfig
)

$ErrorActionPreference = "Continue"

$AppDataDir = [System.IO.Path]::Combine($env:LOCALAPPDATA, "OmenButtonRemapper")
$BinDir = Join-Path $AppDataDir "bin"
$ConfigFile = Join-Path $AppDataDir "config.json"
$TaskName = "OmenButtonRemapperListener"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Uninstalling OMEN Button Remapper" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# 1. Stop background processes
Write-Host "Stopping listener processes..." -ForegroundColor Gray
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null
$Processes = Get-Process -Name "OmenButtonListener" -ErrorAction SilentlyContinue
if ($Processes) {
    $Processes | Stop-Process -Force
    Start-Sleep -Milliseconds 500
    Write-Host "[OK] Listener process stopped." -ForegroundColor Green
}

# 2. Unregister Scheduled Task
Write-Host "Removing scheduled task '$TaskName'..." -ForegroundColor Gray
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
Write-Host "[OK] Scheduled task removed." -ForegroundColor Green

# 3. Remove installed binaries
if (Test-Path $BinDir) {
    Write-Host "Removing installed binaries in $BinDir..." -ForegroundColor Gray
    Remove-Item -Recurse -Force $BinDir -ErrorAction SilentlyContinue
    Write-Host "[OK] Binaries removed." -ForegroundColor Green
}

# 4. Handle configuration
if ($RemoveConfig) {
    if (Test-Path $AppDataDir) {
        Write-Host "Removing all application data and configurations (-RemoveConfig)..." -ForegroundColor Yellow
        Remove-Item -Recurse -Force $AppDataDir -ErrorAction SilentlyContinue
        Write-Host "[OK] Application data removed." -ForegroundColor Green
    }
} else {
    if (Test-Path $ConfigFile) {
        Write-Host "[NOTE] User configuration preserved at $ConfigFile." -ForegroundColor Cyan
        Write-Host "Pass -RemoveConfig to delete saved configurations and logs." -ForegroundColor Gray
    }
}

Write-Host "`nUninstallation complete." -ForegroundColor Green
