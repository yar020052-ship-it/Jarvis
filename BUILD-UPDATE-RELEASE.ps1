#requires -version 5.0
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [string]$Repo = "yar020052-ship-it/Jarvis",
    [switch]$AutoInstall,
    [string]$ChangelogRu = "JARVIS update"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

Push-Location $Root
try {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        $python = Get-Command py -ErrorAction SilentlyContinue
    }
    if (-not $python) {
        throw "Python not found. Install Python and enable Add Python to PATH."
    }

    $args = @(
        "scripts/release.py",
        "--version", $Version,
        "--repo", $Repo,
        "--changelog-ru", $ChangelogRu
    )
    if ($AutoInstall) {
        $args += "--auto-install"
    }

    & $python.Source @args
    if ($LASTEXITCODE -ne 0) {
        throw "Release build failed."
    }

    Write-Host ""
    Write-Host "Upload these files from dist/ to GitHub Release v$($Version.TrimStart('v')):" -ForegroundColor Green
    Get-ChildItem -LiteralPath (Join-Path $Root "dist") | Select-Object Name, Length | Format-Table -AutoSize
    Write-Host "Manifest URL for installers:" -ForegroundColor Cyan
    Write-Host "https://github.com/$Repo/releases/latest/download/latest.json"
} finally {
    Pop-Location
}
