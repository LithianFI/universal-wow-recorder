@echo off
setlocal

REM Get the directory where this script is located
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

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

REM Run the application
python run.py %*