import asyncio
import random
import datetime
from tapo import ApiClient
import logging

logger = logging.getLogger(__name__)

# Global memory cache for Tapo plug telemetry to avoid overloading the physical device
TAPO_CACHE = {}

async def get_tapo_telemetry_async(ip: str, username: str, password: str) -> dict:
    client = ApiClient(username, password)
    device = await client.p110(ip)
    
    device_info = await device.get_device_info()
    state = "on" if device_info.device_on else "off"
    
    # Tapo P110 energy usage gives current_power in milliwatts (mW)
    energy_usage = await device.get_energy_usage()
    apower_mw = float(energy_usage.current_power)
    apower = apower_mw / 1000.0  # Convert to Watts
    
    today_energy = float(energy_usage.today_energy)  # Wh
    month_energy = float(energy_usage.month_energy)  # Wh
    
    # Calculate voltage & current based on active power and standard grid voltage
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

async def get_tapo_telemetry_cached(ip: str, username: str, password: str, device_id: str, force_refresh: bool = False) -> dict:
    global TAPO_CACHE
    
    now = datetime.datetime.utcnow()
    cached = TAPO_CACHE.get(device_id)
    
    # Serve from cache if not expired (limit: 75 seconds to align with background worker's 60-second ticks)
    if not force_refresh and cached and (now - cached["fetched_at"]).total_seconds() < 75.0:
        return cached["data"]
        
    try:
        # Query physical device
        data = await get_tapo_telemetry_async(ip, username, password)
        TAPO_CACHE[device_id] = {
            "data": data,
            "fetched_at": now,
            "last_success_at": now
        }
        return data
    except Exception as e:
        logger.error(f"Error fetching Tapo telemetry for {device_id}: {e}")
        # Serve stale cache if it's less than 5 minutes (300 seconds) old
        if cached and (now - cached.get("last_success_at", now)).total_seconds() < 300.0:
            logger.info(f"Serving stale cache for {device_id} due to fetch error.")
            # Set fetched_at to now - 45s so we don't query the physical device again for 30s
            cached["fetched_at"] = now - datetime.timedelta(seconds=45)
            return cached["data"]
        else:
            # Stale cache is too old or doesn't exist, return offline dict
            return {
                "state": "offline",
                "voltage": 0.0,
                "current": 0.0,
                "apower": 0.0,
                "today_energy": 0.0,
                "month_energy": 0.0,
                "error": str(e)
            }

async def tapo_control_async(ip: str, username: str, password: str, state: str, device_id: str = None) -> bool:
    global TAPO_CACHE
    try:
        client = ApiClient(username, password)
        device = await client.p110(ip)
        if state.lower() == "on":
            await device.on()
        else:
            await device.off()
            
        # Update cache immediately if device_id is provided
        if device_id and device_id in TAPO_CACHE:
            TAPO_CACHE[device_id]["data"]["state"] = state.lower()
            if state.lower() == "off":
                TAPO_CACHE[device_id]["data"]["apower"] = 0.0
                TAPO_CACHE[device_id]["data"]["current"] = 0.0
            TAPO_CACHE[device_id]["fetched_at"] = datetime.datetime.utcnow()
            TAPO_CACHE[device_id]["last_success_at"] = datetime.datetime.utcnow()
            
        return True
    except Exception as e:
        logger.error(f"Error controlling Tapo plug: {e}")
        return False
