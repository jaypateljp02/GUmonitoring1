import os
import time
import asyncio
import logging
import csv
import traceback
from datetime import datetime
import requests
from dotenv import load_dotenv

# plugp100 library imports for P110/P115 KLAP protocol
from plugp100.common.credentials import AuthCredential
from plugp100.new.device_factory import connect, DeviceConnectConfiguration
from plugp100.new.components.energy_component import EnergyComponent
from plugp100.discovery.tapo_discovery import TapoDiscovery

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("tapo-edge-agent")

# Load environment configuration
load_dotenv()

CLOUD_API_URL = os.getenv("CLOUD_API_URL", "https://monitoring-dot-groundup-499909.el.r.appspot.com")
EDGE_API_KEY = os.getenv("EDGE_API_KEY", "factory-tapo-123")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))

# Cache of connections to avoid reconnecting on every single poll
device_clients = {}
# Global configurations list shared between telemetry loop and command loop
active_configs = []
# Track per-device status for heartbeat reporting
device_status = {}  # device_id -> {"status": "ok"|"error", "error": str, "last_success": datetime, "apower": float}

async def get_tapo_telemetry_async(ip: str, username: str, password: str) -> dict:
    """Connect to a physical Tapo plug via local network and retrieve energy telemetry."""
    try:
        credentials = AuthCredential(username, password)
        config = DeviceConnectConfiguration(host=ip, credentials=credentials)
        
        # Connect to device (using cache if already connected)
        if ip in device_clients:
            device = device_clients[ip]
        else:
            device = await connect(config)
            device_clients[ip] = device
            
        await device.update()
        device_on = device.is_on
        state = "on" if device_on else "off"
        
        power_w = 0.0
        today_energy = 0.0
        month_energy = 0.0
        voltage = 230.0
        current = 0.0
        
        energy_comp = device.get_component(EnergyComponent)
        if energy_comp:
            energy_info = energy_comp.energy_info
            power_info = energy_comp.power_info
            
            if energy_info:
                if energy_info.today_energy is not None:
                    today_energy = float(energy_info.today_energy)
                if energy_info.month_energy is not None:
                    month_energy = float(energy_info.month_energy)
                if energy_info.current_power is not None:
                    power_w = float(energy_info.current_power) / 1000.0  # mW to W
                    
            if power_info and power_info.current_power is not None:
                if power_w == 0.0:
                    power_w = float(power_info.current_power)
                    
            unmapped = energy_info.get_unmapped_state() if energy_info else {}
            voltage_mv = unmapped.get("voltage") or unmapped.get("voltage_mv") or unmapped.get("voltage_v")
            current_ma = unmapped.get("current") or unmapped.get("current_ma") or unmapped.get("current_a")
            
            if voltage_mv is not None:
                voltage = float(voltage_mv) / 1000.0 if float(voltage_mv) > 1000 else float(voltage_mv)
            if current_ma is not None:
                current = float(current_ma) / 1000.0 if float(current_ma) > 10 else float(current_ma)
                
        # Clean up and estimate logic if device is off or unmapped values are zero
        if state == "off":
            apower = 0.0
            current = 0.0
        else:
            apower = power_w
            if not voltage or voltage == 0.0:
                voltage = 230.0
            if (not current or current == 0.0) and apower > 0.0:
                current = apower / voltage
                
        return {
            "state": state,
            "voltage": round(voltage, 1),
            "current": round(current, 3),
            "apower": round(apower, 1),
            "today_energy": today_energy,  # in Wh
            "month_energy": month_energy   # in Wh
        }
    except Exception as e:
        # If connection fails, remove from cache to force fresh connect next time
        if ip in device_clients:
            device_clients.pop(ip)
        raise e

def call_api_sync(method: str, url: str, **kwargs) -> requests.Response:
    """Helper to perform blocking HTTP requests synchronously."""
    return requests.request(method, url, **kwargs)

async def fetch_configs_from_cloud() -> list:
    """Fetch the list of Tapo IP configurations from the cloud API."""
    url = f"{CLOUD_API_URL}/sensors/tapo/configs"
    headers = {"X-API-Key": EDGE_API_KEY}
    
    try:
        response = await asyncio.to_thread(
            call_api_sync, "GET", url, headers=headers, timeout=10.0
        )
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 401:
            logger.error("Authentication failed. Check your EDGE_API_KEY.")
        else:
            logger.error(f"Failed to fetch configs: HTTP {response.status_code}")
    except Exception as e:
        logger.error(f"Error connecting to cloud API to fetch configs: {e}")
    return []

async def ingest_telemetry_to_cloud(device_id: str, telemetry_data: dict):
    """Post Tapo telemetry to the cloud API with retry logic."""
    url = f"{CLOUD_API_URL}/sensors/device/{device_id}/plug/ingest"
    headers = {
        "X-API-Key": EDGE_API_KEY,
        "Content-Type": "application/json"
    }
    
    payload = {
        "apower": telemetry_data.get("apower", 0.0),
        "voltage": telemetry_data.get("voltage", 0.0),
        "current": telemetry_data.get("current", 0.0),
        "today_energy": telemetry_data.get("today_energy", 0.0),
        "month_energy": telemetry_data.get("month_energy", 0.0)
    }
    
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            response = await asyncio.to_thread(
                call_api_sync, "POST", url, json=payload, headers=headers, timeout=10.0
            )
            if response.status_code == 200:
                logger.info(f"Ingested telemetry for {device_id} ({telemetry_data['apower']}W)")
                return True
            else:
                logger.error(f"Failed to ingest for {device_id} (Attempt {attempt}/{max_retries}): HTTP {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"Error posting telemetry for {device_id} (Attempt {attempt}/{max_retries}): {e}")
        
        if attempt < max_retries:
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
            
    return False

def write_local_backup(device_id: str, ip: str, data: dict):
    """Save telemetry data locally to CSV as a persistent backup."""
    try:
        data_dir = os.path.join(os.path.dirname(__file__), "data")
        os.makedirs(data_dir, exist_ok=True)
        csv_file = os.path.join(data_dir, "history.csv")
        file_exists = os.path.isfile(csv_file)
        
        headers = [
            "timestamp", "device_id", "ip", "state", 
            "apower_w", "voltage_v", "current_a", 
            "today_energy_wh", "month_energy_wh"
        ]
        
        with open(csv_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(headers)
            writer.writerow([
                datetime.utcnow().isoformat(),
                device_id,
                ip,
                data.get("state"),
                data.get("apower"),
                data.get("voltage"),
                data.get("current"),
                data.get("today_energy"),
                data.get("month_energy")
            ])
    except Exception as e:
        logger.error(f"Failed to write local backup CSV: {e}")

async def execute_local_toggle(ip: str, username: str, password: str, action: str) -> bool:
    """Connect to a local plug and toggle its power state ON or OFF."""
    try:
        credentials = AuthCredential(username, password)
        config = DeviceConnectConfiguration(host=ip, credentials=credentials)
        
        if ip in device_clients:
            device = device_clients[ip]
        else:
            device = await connect(config)
            device_clients[ip] = device
            
        if action.lower() == "on":
            await device.turn_on()
        else:
            await device.turn_off()
        return True
    except Exception as e:
        if ip in device_clients:
            device_clients.pop(ip)
        raise e

async def report_command_status(command_id: str, status: str, error: str = None):
    """Notify the cloud API that a toggle command has finished executing."""
    url = f"{CLOUD_API_URL}/sensors/tapo/commands/{command_id}/status"
    headers = {
        "X-API-Key": EDGE_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {"status": status}
    if error:
        payload["error"] = error
        
    try:
        response = await asyncio.to_thread(
            call_api_sync, "POST", url, json=payload, headers=headers, timeout=5.0
        )
        if response.status_code == 200:
            logger.info(f"Reported command {command_id} status as {status.upper()}")
        else:
            logger.error(f"Failed to report command {command_id} status: HTTP {response.status_code}")
    except Exception as e:
        logger.error(f"Error reporting command status: {e}")

async def execute_pending_commands(configs: list):
    """Fetch toggle commands from the cloud and execute them locally."""
    url = f"{CLOUD_API_URL}/sensors/tapo/commands"
    headers = {"X-API-Key": EDGE_API_KEY}
    
    try:
        response = await asyncio.to_thread(
            call_api_sync, "GET", url, headers=headers, timeout=5.0
        )
        if response.status_code != 200:
            return
            
        commands = response.json()
        if not commands:
            return
            
        for cmd in commands:
            cmd_id = cmd.get("command_id")
            device_id = cmd.get("device_id")
            action = cmd.get("command")
            
            logger.info(f"Received command: Turn {action.upper()} for device {device_id}")
            
            plug_config = None
            for cfg in configs:
                if cfg.get("device_id") == device_id:
                    plug_config = cfg
                    break
                    
            if not plug_config:
                logger.error(f"Cannot execute command: No local IP configuration for device {device_id}")
                await report_command_status(cmd_id, "failed", error="No local configuration found")
                continue
                
            ip = plug_config.get("tapo_ip")
            username = plug_config.get("tapo_username")
            password = plug_config.get("tapo_password")
            
            try:
                success = await execute_local_toggle(ip, username, password, action)
                if success:
                    logger.info(f"Successfully toggled plug at {ip} to {action.upper()}")
                    await report_command_status(cmd_id, "done")
                    
                    # Force immediate poll and telemetry push to GAE so the UI updates instantly
                    asyncio.create_task(poll_and_forward_device(plug_config))
                else:
                    logger.error(f"Failed to toggle plug at {ip}")
                    await report_command_status(cmd_id, "failed", error="Toggle operation failed")
            except Exception as e:
                logger.error(f"Error toggling plug at {ip}: {e}")
                await report_command_status(cmd_id, "failed", error=str(e))
                
    except Exception as e:
        logger.error(f"Error fetching pending commands: {e}")

async def report_heartbeat(cycle_errors: dict):
    """Send a heartbeat to the cloud so we can remotely monitor edge agent health."""
    url = f"{CLOUD_API_URL}/sensors/tapo/heartbeat"
    headers = {
        "X-API-Key": EDGE_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "devices": device_status,
        "cycle_errors": cycle_errors,
        "timestamp": datetime.utcnow().isoformat()
    }
    try:
        response = await asyncio.to_thread(
            call_api_sync, "POST", url, json=payload, headers=headers, timeout=5.0
        )
        if response.status_code == 200:
            logger.info("Heartbeat sent successfully.")
        else:
            logger.warning(f"Heartbeat failed: HTTP {response.status_code}")
    except Exception as e:
        logger.warning(f"Could not send heartbeat: {e}")

async def poll_and_forward_device(config: dict):
    """Handle the lifecycle of polling one device and forwarding its data."""
    device_id = config.get("device_id")
    ip = config.get("tapo_ip")
    username = config.get("tapo_username")
    password = config.get("tapo_password")
    
    logger.info(f"Polling Tapo Plug at {ip} for device {device_id}...")
    try:
        # Query local plug via WiFi (KLAP protocol)
        telemetry = await asyncio.wait_for(
            get_tapo_telemetry_async(ip, username, password), 
            timeout=15.0
        )
        
        # Log and write CSV locally
        write_local_backup(device_id, ip, telemetry)
        
        # Send telemetry to Cloud
        await ingest_telemetry_to_cloud(device_id, telemetry)
        
        # Track success
        device_status[device_id] = {
            "status": "ok",
            "ip": ip,
            "apower": telemetry.get("apower", 0.0),
            "last_success": datetime.utcnow().isoformat(),
            "error": None
        }
        
    except asyncio.TimeoutError:
        err_msg = f"Timeout: Could not reach plug at {ip} within 15 seconds"
        logger.error(err_msg)
        device_status[device_id] = {
            "status": "timeout",
            "ip": ip,
            "error": err_msg,
            "last_success": device_status.get(device_id, {}).get("last_success")
        }
    except Exception as e:
        err_msg = f"{type(e).__name__}: {e}"
        logger.error(f"Error processing device {device_id} at {ip}: {err_msg}")
        device_status[device_id] = {
            "status": "error",
            "ip": ip,
            "error": err_msg,
            "last_success": device_status.get(device_id, {}).get("last_success")
        }

async def command_loop():
    """Concurrently checks for commands from cloud and handles them every 1s."""
    logger.info("Starting Tapo command consumer loop...")
    while True:
        try:
            global active_configs
            if active_configs:
                await execute_pending_commands(active_configs)
        except Exception as e:
            logger.error(f"Exception in command consumer loop: {e}")
        await asyncio.sleep(1.0) # Check every 1.0 seconds for responsive control

async def run_forwarder():
    logger.info("=========================================")
    logger.info("Starting Tapo Edge Forwarder (plugp100)")
    logger.info(f"Targeting Cloud API: {CLOUD_API_URL}")
    logger.info("=========================================")
    
    while True:
        loop_start = time.time()
        
        # 1. Fetch configs from cloud
        logger.info("Fetching Tapo plug configurations from cloud...")
        configs = await fetch_configs_from_cloud()
        
        # 2. Local fallback if cloud is empty/unavailable
        if not configs:
            logger.info("No cloud configurations. Loading fallback from local .env...")
            fallback_ips = os.getenv("TAPO_IPS", os.getenv("TAPO_IP"))
            fallback_device_ids = os.getenv("TAPO_DEVICE_IDS", os.getenv("TAPO_DEVICE_ID"))
            fallback_user = os.getenv("TAPO_USERNAME")
            fallback_pass = os.getenv("TAPO_PASSWORD")
            
            if fallback_ips and fallback_device_ids and fallback_user and fallback_pass:
                ips = [ip.strip() for ip in fallback_ips.split(",")]
                device_ids = [did.strip() for did in fallback_device_ids.split(",")]
                
                configs = []
                for i in range(min(len(ips), len(device_ids))):
                    configs.append({
                        "device_id": device_ids[i],
                        "tapo_ip": ips[i],
                        "tapo_username": fallback_user,
                        "tapo_password": fallback_pass
                    })
                logger.info(f"Successfully loaded {len(configs)} local fallback plug configs.")
            else:
                logger.warning("Local fallback credentials not fully set in .env.")
        
        if configs:
            # Store in global configurations for command consumer loop
            global active_configs
            active_configs = configs
            
            # Poll all devices concurrently
            tasks = [poll_and_forward_device(cfg) for cfg in configs]
            await asyncio.gather(*tasks)
        else:
            logger.warning("No Tapo configurations available. Will retry next cycle.")
            
        # 3. Send heartbeat to cloud with current device statuses
        cycle_errors = {did: ds for did, ds in device_status.items() if ds.get("status") != "ok"}
        await report_heartbeat(cycle_errors)
        
        # 4. Calculate sleep duration to align with POLL_INTERVAL
        elapsed = time.time() - loop_start
        sleep_time = max(1.0, POLL_INTERVAL - elapsed)
        logger.info(f"Cycle completed in {elapsed:.2f}s. Sleeping for {sleep_time:.2f}s...")
        await asyncio.sleep(sleep_time)

async def report_discovered_plugs(plugs: list):
    url = f"{CLOUD_API_URL}/sensors/tapo/discovered"
    headers = {
        "X-API-Key": EDGE_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "devices": [
            {"ip": p.ip, "mac": p.mac, "model": p.device_model}
            for p in plugs
        ]
    }
    try:
        response = await asyncio.to_thread(
            call_api_sync, "POST", url, json=payload, headers=headers, timeout=5.0
        )
        if response.status_code == 200:
            logger.info(f"Reported {len(plugs)} discovered plugs to cloud.")
        else:
            logger.error(f"Failed to report discovered plugs: HTTP {response.status_code}")
    except Exception as e:
        logger.error(f"Error reporting discovered plugs: {e}")

async def discovery_loop():
    logger.info("Starting background Tapo UDP Discovery loop...")
    while True:
        try:
            logger.info("Scanning local network for Tapo plugs...")
            plugs = await TapoDiscovery.scan(timeout=5)
            if plugs:
                await report_discovered_plugs(plugs)
        except Exception as e:
            logger.error(f"Error in discovery loop: {e}")
        # Run discovery scan every 2 minutes
        await asyncio.sleep(120)

async def main():
    await asyncio.gather(
        run_forwarder(),
        command_loop(),
        discovery_loop()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Edge Forwarder stopped by user.")
    except Exception as e:
        logger.critical(f"Unhandled critical crash in main thread: {e}")
        traceback.print_exc()
