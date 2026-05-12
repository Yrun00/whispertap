@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment was not found.
    echo Run install.bat first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" ".\whispertap.py"
pause
