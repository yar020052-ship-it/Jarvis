# Сборка JARVIS single-exe (Windows). Запуск из корня проекта:
#   powershell -ExecutionPolicy Bypass -File build-exe.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
  throw "py launcher not found. Install Python 3.11-3.14 with 'Add to PATH'."
}
# Собирать ОБЯЗАТЕЛЬНО тем python, где стоят зависимости (иначе exe пустой ~15МБ).
# Приоритет: .venv в корне проекта, иначе системный py -3.
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $VenvPy) { $Py = $VenvPy } else { $Py = "py -3" }
& $Py -m pip install --upgrade pyinstaller | Out-Null
& $Py -m PyInstaller jarvis.spec --noconfirm --clean

$Exe = Join-Path $Root "dist\Jarvis.exe"
if (Test-Path $Exe) {
  Write-Host "OK: $Exe" -ForegroundColor Green
  Write-Host ("Size: {0:N1} MB" -f ((Get-Item $Exe).Length / 1MB))
} else {
  throw "Build failed: dist\Jarvis.exe not found. See output above."
}
