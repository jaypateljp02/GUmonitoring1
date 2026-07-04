#!/bin/bash
echo "========================================================"
echo "  Setting up Tapo Edge Forwarder for Raspberry Pi 5"
echo "========================================================"
echo ""

# 1. Update and install dependencies
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv git

# 2. Create virtual environment
if [ ! -d ".venv" ]; then
    echo "[INFO] Creating virtual environment..."
    python3 -m venv .venv
fi

# 3. Install requirements
echo "[INFO] Installing requirements..."
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 4. Create default .env if missing
if [ ! -f ".env" ]; then
    echo "[WARNING] .env not found. Creating default..."
    echo "CLOUD_API_URL=http://157.119.41.61:8003" > .env
    echo "EDGE_API_KEY=factory-tapo-123" >> .env
    echo "POLL_INTERVAL=60" >> .env
    echo "Please edit .env to add your Tapo credentials if needed."
fi

# 5. Create Systemd Service for Auto-Start
SERVICE_FILE="/etc/systemd/system/tapo-edge.service"
echo "[INFO] Creating systemd service to run automatically on boot..."

sudo bash -c "cat > $SERVICE_FILE" <<EOL
[Unit]
Description=Tapo Edge Forwarder
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/.venv/bin/python edge_tapo_forwarder.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOL

sudo systemctl daemon-reload
sudo systemctl enable tapo-edge.service
sudo systemctl start tapo-edge.service

echo ""
echo "========================================================"
echo "[SUCCESS] Installation Complete!"
echo "The Edge Agent is now running in the background and will start on boot."
echo "To check the logs, run: sudo journalctl -u tapo-edge.service -f"
echo "========================================================"
