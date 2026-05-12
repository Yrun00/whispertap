@echo off
cd /d "%~dp0"

net session >nul 2>&1
if not "%errorlevel%"=="0" (
    echo Requesting administrator privileges...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment was not found.
    echo Run install.bat first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" ".\whispertap.py"
pause
