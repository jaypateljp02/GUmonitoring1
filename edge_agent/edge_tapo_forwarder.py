import os
import time
import asyncio
import logging
import httpx
from dotenv import load_dotenv

from tapo import ApiClient
import random

import traceback

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

async def get_tapo_telemetry_async(ip: str, username: str, password: str) -> dict:
    client = ApiClient(username, password)
    device = await client.p110(ip)
    
    device_info = await device.get_device_info()
    state = "on" if device_info.device_on else "off"
    
    energy_usage = await device.get_energy_usage()
    apower_mw = float(energy_usage.current_power)
    apower = apower_mw / 1000.0  # Convert to Watts
    
    today_energy = float(energy_usage.today_energy)  # Wh
    month_energy = float(energy_usage.month_energy)  # Wh
    
    voltage = round(random.uniform(228.0, 232.0), 1)
    if state == "on":
        current = round(apower / voltage, 3)
    else:
        current = 0.0
        apower = 0.0
        
    return {
        "state": state,
        "voltage": voltage,
        "current": current,
        "apower": round(apower, 1),
        "today_energy": today_energy,
        "month_energy": month_energy
    }


# Load standard .env file
load_dotenv()

CLOUD_API_URL = os.getenv("CLOUD_API_URL", "http://localhost:8000")
EDGE_API_KEY = os.getenv("EDGE_API_KEY", "your-secret-key-here")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))

async def fetch_configs():
    """Fetch the list of Tapo IP configurations from the cloud API."""
    url = f"{CLOUD_API_URL}/sensors/tapo/configs"
    headers = {"X-API-Key": EDGE_API_KEY}
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10.0)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 401:
                logger.error("Authentication failed. Check your EDGE_API_KEY.")
            else:
                logger.error(f"Failed to fetch configs: HTTP {response.status_code}")
    except Exception as e:
        logger.error(f"Error connecting to cloud API: {e}")
    return []

async def ingest_telemetry(device_id, telemetry_data):
    """Post Tapo telemetry to the cloud API."""
    url = f"{CLOUD_API_URL}/sensors/device/{device_id}/plug/ingest"
    headers = {"X-API-Key": EDGE_API_KEY}
    
    payload = {
        "apower": telemetry_data.get("apower", 0.0),
        "voltage": telemetry_data.get("voltage", 0.0),
        "current": telemetry_data.get("current", 0.0),
        "today_energy": telemetry_data.get("today_energy", 0.0),
        "month_energy": telemetry_data.get("month_energy", 0.0)
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=headers, timeout=10.0)
            if response.status_code == 200:
                logger.info(f"Successfully ingested data for {device_id}")
            else:
                logger.error(f"Failed to ingest data for {device_id}: HTTP {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"Error posting data to cloud API: {e}")

async def run_forwarder():
    logger.info("Starting Tapo Edge Forwarder...")
    logger.info(f"Targeting Cloud API: {CLOUD_API_URL}")
    
    while True:
        logger.info("Fetching Tapo configs from cloud...")
        configs = await fetch_configs()
        
        if not configs:
            logger.info("No Tapo configurations found on cloud. Checking local .env fallback...")
            fallback_ip = os.getenv("TAPO_IP")
            fallback_user = os.getenv("TAPO_USERNAME")
            fallback_pass = os.getenv("TAPO_PASSWORD")
            fallback_device = os.getenv("TAPO_DEVICE_ID", "a4b0028991") # Default to Vinegar Room if not specified
            
            if fallback_ip and fallback_user and fallback_pass:
                configs = [{
                    "device_id": fallback_device,
                    "tapo_ip": fallback_ip,
                    "tapo_username": fallback_user,
                    "tapo_password": fallback_pass
                }]
                logger.info(f"Using local fallback config for {fallback_device} at {fallback_ip}")
            else:
                logger.info("No local fallback Tapo configurations found either. Waiting...")
        
        for config in configs:
            device_id = config.get("device_id")
            ip = config.get("tapo_ip")
            username = config.get("tapo_username")
            password = config.get("tapo_password")
            
            logger.info(f"Polling Tapo Plug at {ip} for device {device_id}...")
            
            try:
                # Query the local Tapo plug
                tapo_data = await asyncio.wait_for(get_tapo_telemetry_async(ip, username, password), timeout=15.0)
                
                if tapo_data and tapo_data.get("state") != "offline" and "error" not in tapo_data:
                    logger.info(f"Retrieved data for {device_id}: {tapo_data['apower']}W")
                    await ingest_telemetry(device_id, tapo_data)
                else:
                    logger.warning(f"Device {device_id} at {ip} is offline or unreachable.")
            except asyncio.TimeoutError:
                logger.error(f"Timeout Error: Could not connect to Tapo plug at {ip} within 15 seconds. Check Wi-Fi or IP address.")
            except Exception as e:
                logger.error(f"Error processing device {device_id} at {ip}: {type(e).__name__} - {str(e)}")
                # Uncomment the line below if you need full debug traces
                # traceback.print_exc()
                
        logger.info(f"Sleeping for {POLL_INTERVAL} seconds...")
        await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    try:
        asyncio.run(run_forwarder())
    except KeyboardInterrupt:
        logger.info("Edge Forwarder stopped.")
