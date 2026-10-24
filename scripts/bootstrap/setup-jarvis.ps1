#requires -version 5.0
<#
.SYNOPSIS
    JARVIS bootstrapper (Windows) - автоопределение и установка/обновление
.DESCRIPTION
    Скачивает только нужное под этот ПК. Не трогает пользовательские данные.
#>
param(
    [string]$ManifestUrl = "https://github.com/yar020052-ship-it/Jarvis/releases/latest/download/latest.json",
    [string]$Channel = "stable",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

function Write-Info { param([string]$m) Write-Host "[INFO] $m" -ForegroundColor Cyan }
function Write-Ok   { param([string]$m) Write-Host "[OK]   $m" -ForegroundColor Green }
function Write-Warn { param([string]$m) Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Write-Err  { param([string]$m) Write-Host "[ERR]  $m" -ForegroundColor Red }

# Detect
$arch = $env:PROCESSOR_ARCHITECTURE
if ($arch -eq "AMD64") { $archKey = "x64" } elseif ($arch -eq "ARM64") { $archKey = "arm64" } else { $archKey = $arch.ToLower() }
$plat = "windows-$archKey"
Write-Info "Platform: $plat"

# Temp
$Tmp = "$env:TEMP\Jarvis-Bootstrap"
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null

# Get manifest
try {
  Write-Info "Fetching manifest: $ManifestUrl"
  $manifest = Invoke-RestMethod -Uri $ManifestUrl -TimeoutSec 15
  $ver = $manifest.version
  Write-Ok "Latest stable: $ver"
} catch {
  Write-Err "Failed to fetch manifest: $_"
  exit 1
}

# App dir
$appDir = "$env:LOCALAPPDATA\Programs\Jarvis"
if (-not (Test-Path $appDir)) {
  New-Item -ItemType Directory -Force -Path $appDir | Out-Null
}

# Download
$plats = $manifest.platforms
$p = $plats.$plat
if (-not $p) {
  # try underscore
  $p = $plats.($plat -replace '-', '_')
}
if (-not $p) {
  Write-Err "No build for $plat in manifest"
  exit 1
}
$url = $p.url
$sha = $p.sha256
$zip = Join-Path $Tmp "Jarvis-$ver-$plat.zip"
Write-Info "Downloading: $url"
Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
Write-Ok "Downloaded: $([math]::Round((Get-Item $zip).Length/1MB,1)) MB"

# SHA256
if ($sha) {
  $actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
  if ($actual -ne $sha.ToLower()) {
    Write-Err "SHA256 mismatch"
    exit 1
  }
  Write-Ok "SHA256 verified"
}

# Extract
$upd = Join-Path $appDir "scripts\update.py"
if (-not (Test-Path $upd)) {
  # extract to appDir root preserving user-data not touched by updater? but first time
  Expand-Archive -Path $zip -DestinationPath $appDir -Force
  Write-Ok "Installed to $appDir"
} else {
  Write-Info "Running updater..."
  $python = Join-Path $appDir ".venv\Scripts\python.exe"
  if (-not (Test-Path $python)) {
    # try system python
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
  }
  if (-not $python) { $python = "py" }
  & $python $upd --app-dir $appDir --zip $zip
}

# Create shortcut
$start = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Jarvis.lnk"
$wsh = New-Object -ComObject WScript.Shell
$lnk = $wsh.CreateShortcut($start)
$lnk.TargetPath = "wscript.exe"
$lnk.Arguments = """$appDir\launch_hidden.vbs"""
$lnk.WorkingDirectory = $appDir
$lnk.IconLocation = "$appDir\config\jarvis.ico"
$lnk.Save()

Write-Ok "Shortcut created in Start Menu"
Write-Ok "Done. Launch: $appDir\launch_hidden.vbs"
