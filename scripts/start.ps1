# Локальный запуск JARVIS
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  $python = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $python) { $python = "py" }
Set-Location $root
& $python main.py
