@echo off
title Tapo Edge Forwarder
echo ========================================================
echo   Starting Tapo Edge Forwarder Setup & Launch Loop
echo ========================================================
echo.

:: 1. Check Python installation
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in system PATH.
    echo Please install Python 3.10+ and select 'Add Python to PATH'.
    pause
    exit /b 1
)

:: 2. Check virtual environment portability
if exist .venv (
    .venv\Scripts\python -c "import sys" >nul 2>&1
    if errorlevel 1 (
        echo ========================================================
        echo [!] Detected that .venv was copied from a different path.
        echo [!] Re-creating local virtual environment...
        echo ========================================================
        rmdir /s /q .venv
    )
)

:: 3. Create virtual environment if missing
if not exist .venv (
    echo [INFO] Creating virtual environment .venv...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: 4. Activate virtual environment and install requirements
echo [INFO] Activating virtual environment...
call .venv\Scripts\activate

echo [INFO] Upgrading pip...
python -m pip install --upgrade pip >nul 2>&1

echo [INFO] Installing/verifying requirements...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

:: 5. Create default .env if not exists
if not exist .env (
    echo [WARNING] .env configuration file not found!
    echo Creating default .env fallback...
    (
        echo CLOUD_API_URL=https://gu-monitoring.initiativesewafoundation.com
        echo EDGE_API_KEY=factory-tapo-123
        echo POLL_INTERVAL=60
        echo TAPO_USERNAME=saannkket@gmail.com
        echo TAPO_PASSWORD=Sanket@007
        echo TAPO_IPS=192.168.0.109,192.168.0.107
        echo TAPO_DEVICE_IDS=a4b0028d6e,a4b0028991
    ) > .env
    echo Default .env created. Please adjust credentials if necessary.
)

:: 5.5 Pull latest code updates from Git on startup
echo [INFO] Pulling latest code changes from GitHub...
git pull

:: 6. Launch forwarder in an infinite restart loop
:run_loop
echo.
echo ========================================================
echo [INFO] Starting Tapo Edge Forwarder at %time%
echo ========================================================
echo.
python edge_tapo_forwarder.py

echo.
echo ========================================================
echo [WARNING] Edge Forwarder stopped or crashed.
echo [INFO] Restarting in 10 seconds... Press Ctrl+C to cancel.
echo ========================================================
timeout /t 10
goto run_loop
