@echo off
echo Checking for Python...
python --version >nul 2>&1
if errorlevel 1 goto install_python
goto start_app

:install_python
echo Python is not installed. Downloading Python...
curl -o python_installer.exe https://www.python.org/ftp/python/3.11.6/python-3.11.6-amd64.exe
echo Installing Python silently (this may take a few minutes)...
start /wait python_installer.exe /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
echo.
echo ========================================================
echo Python has been successfully installed!
echo IMPORTANT: You must CLOSE this window and double-click 
echo run_edge.bat AGAIN for the changes to take effect.
echo ========================================================
pause
exit

:start_app
echo Installing requirements (if not already installed)...
pip install -r requirements.txt
echo.
echo Starting Tapo Edge Forwarder...
python edge_tapo_forwarder.py
pause
