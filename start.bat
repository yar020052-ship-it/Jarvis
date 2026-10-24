@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo .venv not found. Create it with: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
.\.venv\Scripts\python.exe main.py
pause