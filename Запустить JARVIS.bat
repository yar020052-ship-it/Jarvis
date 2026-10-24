@echo off
cd /d "%~dp0"
start "" /b "%SystemRoot%\System32\wscript.exe" //B //NoLogo "%~dp0launch_hidden.vbs"
exit /b
