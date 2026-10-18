<#
.SYNOPSIS
    Builds OmenButtonConfigurator.exe and OmenButtonListener.exe using PyInstaller.
.DESCRIPTION
    Compiles the PySide6 GUI Configurator and the lightweight background WMI Listener
    into independent standalone executables in the dist/ directory.
.PARAMETER Clean
    Removes previous build and dist directories before packaging.
#>
[CmdletBinding()]
param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$DistDir = Join-Path $ProjectRoot "dist"
$BuildDir = Join-Path $ProjectRoot "build"
$AssetsDir = Join-Path $ProjectRoot "assets"
$IconPath = Join-Path $AssetsDir "app_icon.ico"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Building OMEN Button Remapper Executables" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Project Root : $ProjectRoot"
Write-Host "Output Dir   : $DistDir"

# Stop running listener process if active to allow replacing binary
$Running = Get-Process -Name "OmenButtonListener" -ErrorAction SilentlyContinue
if ($Running) {
    Write-Host "Stopping running OmenButtonListener instance before build..." -ForegroundColor Yellow
    $Running | Stop-Process -Force
    Start-Sleep -Milliseconds 600
}

if ($Clean) {
    Write-Host "`nCleaning build artifacts..." -ForegroundColor Yellow
    if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir }
    if (Test-Path $DistDir) { Remove-Item -Recurse -Force $DistDir }
}

if (-not (Test-Path $DistDir)) {
    New-Item -ItemType Directory -Path $DistDir -Force | Out-Null
}

# 1. Build OmenButtonListener.exe (Lightweight, noconsole, NO PySide6 dependency)
Write-Host "`n[1/2] Building OmenButtonListener.exe..." -ForegroundColor Green
$ListenerScript = Join-Path $ProjectRoot "src\listener.py"

$ListenerArgs = @(
    "--noconfirm",
    "--onefile",
    "--noconsole",
    "--name", "OmenButtonListener",
    "--distpath", $DistDir,
    "--workpath", (Join-Path $BuildDir "listener"),
    "--paths", (Join-Path $ProjectRoot "src")
)

if (Test-Path $IconPath) {
    $ListenerArgs += @("--icon", $IconPath)
}

$ListenerArgs += $ListenerScript

& pyinstaller @ListenerArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed while building OmenButtonListener.exe"
}

# 2. Build OmenButtonConfigurator.exe (PySide6 GUI, windowed, with bundled assets)
Write-Host "`n[2/2] Building OmenButtonConfigurator.exe..." -ForegroundColor Green
$ConfiguratorScript = Join-Path $ProjectRoot "src\configurator.py"

$ConfiguratorArgs = @(
    "--noconfirm",
    "--onefile",
    "--windowed",
    "--name", "OmenButtonConfigurator",
    "--distpath", $DistDir,
    "--workpath", (Join-Path $BuildDir "configurator"),
    "--paths", (Join-Path $ProjectRoot "src")
)

if (Test-Path $IconPath) {
    $ConfiguratorArgs += @(
        "--icon", $IconPath,
        "--add-data", "$IconPath;assets"
    )
}

$ConfiguratorArgs += $ConfiguratorScript

& pyinstaller @ConfiguratorArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed while building OmenButtonConfigurator.exe"
}

# Verification
$ListenerExe = Join-Path $DistDir "OmenButtonListener.exe"
$ConfiguratorExe = Join-Path $DistDir "OmenButtonConfigurator.exe"

Write-Host "`n==========================================" -ForegroundColor Cyan
Write-Host " Build Summary" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

if ((Test-Path $ListenerExe) -and (Test-Path $ConfiguratorExe)) {
    $ListenerSizeMB = [math]::Round((Get-Item $ListenerExe).Length / 1MB, 2)
    $ConfiguratorSizeMB = [math]::Round((Get-Item $ConfiguratorExe).Length / 1MB, 2)

    Write-Host " [OK] OmenButtonListener.exe      : $ListenerSizeMB MB -> $ListenerExe" -ForegroundColor Green
    Write-Host " [OK] OmenButtonConfigurator.exe  : $ConfiguratorSizeMB MB -> $ConfiguratorExe" -ForegroundColor Green
    Write-Host "`nBuild completed successfully!" -ForegroundColor Green
} else {
    throw "Build verification failed: one or more executables were not produced."
}
