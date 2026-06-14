@echo off
echo Installing requirements (if not already installed)...
pip install -r requirements.txt
echo.
echo Starting Tapo Edge Forwarder...
python edge_tapo_forwarder.py
pause
