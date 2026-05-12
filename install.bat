@echo off
setlocal
cd /d "%~dp0"

echo WhisperTap setup
echo.

py -3.11 --version >nul 2>&1
if errorlevel 1 (
    echo Python 3.11 was not found.
    echo Install Python 3.11 from https://www.python.org/downloads/windows/
    echo Make sure "py -3.11 --version" works, then run this file again.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3.11 -m venv .venv
    if errorlevel 1 (
        echo Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo Updating pip...
".venv\Scripts\python.exe" -m pip install -U pip wheel
if errorlevel 1 (
    echo Failed to update pip.
    pause
    exit /b 1
)

echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo Done. Run start_whispertap.bat to start WhisperTap.
pause
