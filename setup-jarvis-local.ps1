#requires -version 5.0
<#
.SYNOPSIS
    Local Windows installer for JARVIS.
.DESCRIPTION
    Run this script from the root of the unpacked JARVIS ZIP.
    It copies the app to %LOCALAPPDATA%\Programs\Jarvis, creates a virtual
    environment, installs/updates pip, and creates Start Menu/Desktop launchers.
#>
param(
    [string]$InstallDir = "$env:LOCALAPPDATA\Programs\Jarvis",
    [string]$ManifestUrl = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

function Write-Info { param([string]$Message) Write-Host "[INFO] $Message" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Message) Write-Host "[OK]   $Message" -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host "[WARN] $Message" -ForegroundColor Yellow }
function Write-Err  { param([string]$Message) Write-Host "[ERR]  $Message" -ForegroundColor Red }

function Find-Python {
    $candidates = @()

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        try {
            & py -3 --version *> $null
            if ($LASTEXITCODE -eq 0) { return @("py", "-3") }
        } catch {}
    }

    foreach ($name in @("python", "python3")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { $candidates += $cmd.Source }
    }

    foreach ($candidate in $candidates) {
        try {
            & $candidate --version *> $null
            if ($LASTEXITCODE -eq 0) { return @($candidate) }
        } catch {}
    }

    return $null
}

function Copy-App {
    param(
        [string]$Source,
        [string]$Destination
    )

    $exclude = @(
        ".git", ".venv", "__pycache__", "logs", "cache", "dist",
        "received_errors", "setup-jarvis-local.ps1"
    )

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null

    $robocopyArgs = @(
        $Source,
        $Destination,
        "/E",
        "/XD"
    ) + $exclude + @(
        "/XF",
        "*.pyc", "*.pyo", "*.bak",
        "/R:2",
        "/W:1",
        "/NFL",
        "/NDL",
        "/NP"
    )

    & robocopy @robocopyArgs | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "robocopy failed with exit code $LASTEXITCODE"
    }
}

function Write-LauncherFiles {
    param(
        [string]$AppDir,
        [string]$PythonExe
    )

    $batPath = Join-Path $AppDir "run-jarvis.bat"
    $bat = @"
@echo off
cd /d "%~dp0"
"$PythonExe" main.py
pause
"@
    Set-Content -LiteralPath $batPath -Value $bat -Encoding ASCII

    $vbsPath = Join-Path $AppDir "launch_hidden.vbs"
    $vbs = @"
Set shell = CreateObject("WScript.Shell")
appDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = appDir
shell.Run Chr(34) & "$PythonExe" & Chr(34) & " main.py", 0, False
"@
    Set-Content -LiteralPath $vbsPath -Value $vbs -Encoding ASCII

    return @{
        Bat = $batPath
        Vbs = $vbsPath
    }
}

function New-Shortcut {
    param(
        [string]$Path,
        [string]$TargetPath,
        [string]$Arguments,
        [string]$WorkingDirectory,
        [string]$IconLocation
    )

    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null

    $wsh = New-Object -ComObject WScript.Shell
    $shortcut = $wsh.CreateShortcut($Path)
    $shortcut.TargetPath = $TargetPath
    $shortcut.Arguments = $Arguments
    $shortcut.WorkingDirectory = $WorkingDirectory
    if ($IconLocation -and (Test-Path $IconLocation)) {
        $shortcut.IconLocation = $IconLocation
    }
    $shortcut.Save()
}

try {
    $sourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    if (-not (Test-Path (Join-Path $sourceDir "main.py"))) {
        throw "main.py was not found. Run this script from the root of the unpacked JARVIS folder."
    }

    Write-Info "JARVIS source: $sourceDir"
    Write-Info "Install dir: $InstallDir"
    if ($ManifestUrl) {
        Write-Info "Update manifest: $ManifestUrl"
    }

    if ((Test-Path $InstallDir) -and -not $Force) {
        Write-Warn "Existing installation found. Files will be updated, user config will be kept."
    }

    $python = Find-Python
    if (-not $python) {
        Write-Err "Python 3 was not found."
        Write-Host "Install Python from https://www.python.org/downloads/windows/ and enable 'Add Python to PATH'."
        exit 1
    }

    Write-Ok "Python found: $($python -join ' ')"

    Write-Info "Copying application files..."
    Copy-App -Source $sourceDir -Destination $InstallDir
    Write-Ok "Files copied"

    if ($ManifestUrl) {
        $configDir = Join-Path $InstallDir "config"
        New-Item -ItemType Directory -Force -Path $configDir | Out-Null
        Set-Content -LiteralPath (Join-Path $configDir "update_manifest_url.txt") -Value $ManifestUrl -Encoding UTF8
        Write-Ok "Update channel configured"
    }

    $venvDir = Join-Path $InstallDir ".venv"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"

    if (-not (Test-Path $venvPython)) {
        Write-Info "Creating virtual environment..."
        $pythonExe = $python[0]
        $pythonArgs = @()
        if ($python.Length -gt 1) {
            $pythonArgs = $python[1..($python.Length - 1)]
        }
        & $pythonExe @pythonArgs -m venv $venvDir
        if ($LASTEXITCODE -ne 0) { throw "Failed to create virtual environment." }
        Write-Ok "Virtual environment created"
    } else {
        Write-Ok "Virtual environment already exists"
    }

    Write-Info "Updating pip..."
    & $venvPython -m pip install --upgrade pip --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { Write-Warn "pip upgrade failed; continuing with existing pip." }

    Write-Info "Installing core dependencies. This can take several minutes..."
    $installCode = @"
from core.installer import install_for_config
install_for_config({"stt_engine": "whisper", "tts_engine": "edgetts"}, print)
"@
    Push-Location $InstallDir
    try {
        & $venvPython -c $installCode
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "Some dependencies may not have installed. JARVIS will try again on launch."
        }
    } finally {
        Pop-Location
    }

    $launchers = Write-LauncherFiles -AppDir $InstallDir -PythonExe $venvPython
    $icon = Join-Path $InstallDir "config\jarvis.ico"

    $desktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "JARVIS.lnk"
    New-Shortcut -Path $desktopShortcut -TargetPath $launchers.Bat -Arguments "" -WorkingDirectory $InstallDir -IconLocation $icon

    $startShortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\JARVIS.lnk"
    New-Shortcut -Path $startShortcut -TargetPath "wscript.exe" -Arguments "`"$($launchers.Vbs)`"" -WorkingDirectory $InstallDir -IconLocation $icon

    Write-Ok "Desktop shortcut: $desktopShortcut"
    Write-Ok "Start Menu shortcut: $startShortcut"
    Write-Ok "Done. Launch JARVIS from the Desktop shortcut or run:"
    Write-Host "      $($launchers.Bat)"
} catch {
    Write-Err $_.Exception.Message
    Write-Host ""
    Write-Host "Installation failed. You can send this window text to the developer."
    exit 1
}
