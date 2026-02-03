@echo off
setlocal

REM ============================================================================
REM WoW Raid Recorder Launcher for Windows
REM ============================================================================

REM Get the directory where this script is located
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

echo =========================================
echo    WoW Raid Recorder Launcher
echo =========================================

REM Check if virtual environment exists
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Install requirements if needed
if not exist "venv\.requirements_installed" (
    echo Installing requirements...
    pip install -r requirements.txt
    echo. > venv\.requirements_installed
)

echo.
echo Starting WoW Raid Recorder...
echo Web interface: http://localhost:5001
echo Press Ctrl+C to stop the application
echo.

REM Open browser automatically after delay
echo Opening browser...
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:5001"

REM Run the application
python run.py %*