@echo off
title Ground Up Monitoring - Factory Local Server
color 0A
echo ========================================================
echo Ground Up Factory Local Server
echo ========================================================
echo.

:: Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed.
    echo Please install Python 3.10 or higher from Microsoft Store or python.org.
    echo Make sure to check "Add Python to PATH" during installation!
    pause
    exit /b
)

:: Create Virtual Environment
if not exist "venv" (
    echo [INFO] Creating virtual environment...
    python -m venv venv
)

:: Install Dependencies
echo [INFO] Installing requirements...
call venv\Scripts\activate.bat
pip install -r requirements.txt >nul 2>&1
if exist "edge_agent\requirements.txt" (
    pip install -r edge_agent\requirements.txt >nul 2>&1
)

:: Set up .env file
if not exist "backend\.env" (
    echo [SETUP] First-time setup! We need your eWeLink credentials.
    echo These will be saved locally on this laptop.
    setlocal enabledelayedexpansion
    set /p EWELINK_EMAIL="Enter eWeLink Email: "
    set /p EWELINK_PASSWORD="Enter eWeLink Password: "
    
    echo EWELINK_EMAIL=!EWELINK_EMAIL!> backend\.env
    echo EWELINK_PASSWORD=!EWELINK_PASSWORD!>> backend\.env
    echo DATABASE_URL=sqlite:///monitoring.db>> backend\.env
    endlocal
    
    echo [SUCCESS] Credentials saved to backend\.env!
)

:: Start the server
echo ========================================================
echo Starting the monitoring server!
echo Keep this window open. 
echo To view the dashboard, open Google Chrome and go to:
echo http://localhost:8000
echo ========================================================
echo.

python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
pause
