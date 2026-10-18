<#
.SYNOPSIS
    Installs OmenButtonListener as a user-level background process managed by Windows Task Scheduler.
.DESCRIPTION
    1. Creates the %LOCALAPPDATA%\OmenButtonRemapper directory structure.
    2. Installs the listener (and configurator) executables into a stable application location.
    3. Preserves any existing valid configuration, creating default config if needed.
    4. Registers or updates the scheduled task 'OmenButtonRemapperListener' with logon trigger.
    5. Starts the listener and verifies running status.
.PARAMETER SkipStart
    Installs the task and files without starting the listener immediately.
#>
[CmdletBinding()]
param(
    [switch]$SkipStart
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$DistDir = Join-Path $ProjectRoot "dist"

$AppDataDir = [System.IO.Path]::Combine($env:LOCALAPPDATA, "OmenButtonRemapper")
$BinDir = Join-Path $AppDataDir "bin"
$LogsDir = Join-Path $AppDataDir "logs"
$ConfigFile = Join-Path $AppDataDir "config.json"
$TaskName = "OmenButtonRemapperListener"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Installing OMEN Button Remapper Listener" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Target AppData Dir : $AppDataDir"
Write-Host "Binary Dir         : $BinDir"

# 1. Create directories
if (-not (Test-Path $BinDir)) { New-Item -ItemType Directory -Path $BinDir -Force | Out-Null }
if (-not (Test-Path $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null }

# 2. Locate source executables
$SourceListener = Join-Path $DistDir "OmenButtonListener.exe"
$SourceConfigurator = Join-Path $DistDir "OmenButtonConfigurator.exe"

if (-not (Test-Path $SourceListener)) {
    throw "OmenButtonListener.exe was not found in '$DistDir'. Please run scripts\build.ps1 first."
}

# Stop running listener process before overwriting binary
$Running = Get-Process -Name "OmenButtonListener" -ErrorAction SilentlyContinue
if ($Running) {
    Write-Host "Stopping running OmenButtonListener instance..." -ForegroundColor Yellow
    $Running | Stop-Process -Force
    Start-Sleep -Milliseconds 800
}

# Copy executables
Copy-Item -Path $SourceListener -Destination $BinDir -Force
$InstalledListener = Join-Path $BinDir "OmenButtonListener.exe"
Write-Host "[OK] Installed $InstalledListener" -ForegroundColor Green

if (Test-Path $SourceConfigurator) {
    Copy-Item -Path $SourceConfigurator -Destination $BinDir -Force
    Write-Host "[OK] Installed $(Join-Path $BinDir 'OmenButtonConfigurator.exe')" -ForegroundColor Green
}

# 3. Preserve or create configuration
if (-not (Test-Path $ConfigFile)) {
    Write-Host "Creating initial default configuration at $ConfigFile..." -ForegroundColor Gray
    $DefaultConfig = @{
        version = 1
        selected_app = $null
    } | ConvertTo-Json -Depth 4
    [System.IO.File]::WriteAllText($ConfigFile, $DefaultConfig, [System.Text.Encoding]::UTF8)
} else {
    Write-Host "[OK] Existing configuration preserved at $ConfigFile" -ForegroundColor Green
}

# 4. Register or update scheduled task
Write-Host "Registering scheduled task '$TaskName' (Trigger: At user sign-in)..." -ForegroundColor Gray
$Action = New-ScheduledTaskAction -Execute $InstalledListener -WorkingDirectory $BinDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -Hidden

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force -ErrorAction Stop | Out-Null
Write-Host "[OK] Scheduled task '$TaskName' registered successfully." -ForegroundColor Green

# 5. Start listener if not skipped
if (-not $SkipStart) {
    Write-Host "Starting listener..." -ForegroundColor Gray
    Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1

    $Proc = Get-Process -Name "OmenButtonListener" -ErrorAction SilentlyContinue
    if ($Proc) {
        Write-Host "[OK] Listener is running in user session (PID: $($Proc.Id))." -ForegroundColor Green
    } else {
        Write-Host "[WARNING] Listener task was started, but process was not detected immediately." -ForegroundColor Yellow
        Write-Host "Check logs at: $(Join-Path $LogsDir 'listener.log')" -ForegroundColor Yellow
    }
}

Write-Host "`nInstallation finished successfully!" -ForegroundColor Green
Write-Host "To configure button actions, run OmenButtonConfigurator.exe" -ForegroundColor Cyan
