@echo off
title Ground Up Monitoring - Factory Local Server
color 0A
echo ========================================================
echo Ground Up Factory Local Server
echo ========================================================
echo.

:: Add Python 3.11 to PATH if needed
set PATH=C:\Users\User\AppData\Local\Programs\Python\Python311;C:\Users\User\AppData\Local\Programs\Python\Python311\Scripts;%PATH%

:: Check for Virtual Environment
if not exist "venv\Scripts\python.exe" (
    echo [INFO] Creating virtual environment...
    python -m venv venv
    call venv\Scripts\activate.bat
    echo [INFO] Installing requirements...
    pip install -r requirements.txt
    if exist "edge_agent\requirements.txt" (
        pip install -r edge_agent\requirements.txt
    )
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
    echo DATABASE_URL=postgresql://postgres:1234@localhost:5432/groundup>> backend\.env
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

venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
pause

