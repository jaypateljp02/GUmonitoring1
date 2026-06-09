import os
import time
import random
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv

from backend.database import SessionLocal
from backend.models.device_telemetry import DeviceTelemetry
from backend.models.sensor import Sensor
from backend.models.reading import SensorReading
from backend.models.alert import Alert
from backend.services.ewelink import EwelinkClient

load_dotenv()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

class WorkerState:
    MOCK_STATES = {}  # dict mapping device_id -> mode ('normal', 'ice', 'warm', 'failover')

async def ingestion_loop():
    email = os.getenv("EWELINK_EMAIL")
    password = os.getenv("EWELINK_PASSWORD")
    region = os.getenv("EWELINK_REGION", "as")
    
    # All 3 monitored devices
    DEVICES = [
        {"id": "a4b002884e", "name": "Device 1"},
        {"id": "a4b002898f", "name": "Miso Room"},
        {"id": "a4b0028991", "name": "Vinegar Room"},
    ]
    
    client = None
    use_live = False
    
    if email and password:
        logger.info("Initializing eWeLink client...")
        client = EwelinkClient(email=email, password=password, region=region)
        use_live = True
    else:
        logger.warning("No EWELINK_EMAIL in .env. Starting Simulator Mode.")
 
    while True:
        start_time = time.time()
        try:
            # 1. Fetch all active sensors from the database
            with SessionLocal() as db:
                active_sensors = db.query(Sensor).filter(Sensor.active == True).all()

            # Group sensors by device_id
            devices_map = {}
            for s in active_sensors:
                if s.device_id:
                    if s.device_id not in devices_map:
                        devices_map[s.device_id] = []
                    devices_map[s.device_id].append(s)

            for target_device, sensors in devices_map.items():
                try:
                    # Determine mode from first sensor (all sensors on same device share the mode)
                    mode = sensors[0].mock_mode if sensors else "normal"

                    if mode == "failover":
                        logger.info(f"Simulator in failover mode for device {target_device}. No data ingested.")
                        continue

                    temp_val = None
                    hum_val = None
                    bat_val = 100.0

                    if use_live and mode == "normal":
                        status = await client.get_device_status(target_device)
                        if status:
                            temp_val = status.get("temperature")
                            hum_val = status.get("humidity")
                            bat_val = status.get("battery", 100.0)
                    else:
                        # Simulator mode logic
                        if mode == "ice":
                            temp_val = round(random.uniform(-5.0, -2.0), 2)
                        elif mode == "warm":
                            temp_val = round(random.uniform(5.0, 10.0), 2)
                        else:
                            temp_val = round(random.uniform(2.0, 3.5), 2)
                        
                        hum_val = round(random.uniform(40.0, 60.0), 2)
                        bat_val = 98.0

                    if temp_val is not None and hum_val is not None:
                        with SessionLocal() as db:
                            # 1. Raw Telemetry
                            telemetry = DeviceTelemetry(
                                device_id=target_device,
                                temperature=temp_val,
                                humidity=hum_val,
                                battery_level=bat_val
                            )
                            db.add(telemetry)
                            
                            # 2. Map to logical sensors
                            for s in sensors:
                                val = temp_val if s.type == "temperature" else hum_val
                                reading = SensorReading(sensor_id=s.id, value=val)
                                db.add(reading)
                                
                                # 3. Check thresholds
                                if s.max_threshold is not None and val > s.max_threshold:
                                    recent_alert = db.query(Alert).filter(Alert.sensor_id == s.id, Alert.resolved == False).first()
                                    if not recent_alert:
                                        logger.warning(f"THRESHOLD EXCEEDED for sensor {s.id}: {val} > {s.max_threshold}")
                                        new_alert = Alert(
                                            sensor_id=s.id,
                                            value=val,
                                            message=f"{s.type.capitalize()} threshold exceeded: {val} > {s.max_threshold}"
                                        )
                                        db.add(new_alert)
                                
                                if s.min_threshold is not None and val < s.min_threshold:
                                    recent_alert = db.query(Alert).filter(Alert.sensor_id == s.id, Alert.resolved == False).first()
                                    if not recent_alert:
                                        logger.warning(f"THRESHOLD DROPPED for sensor {s.id}: {val} < {s.min_threshold}")
                                        new_alert = Alert(
                                            sensor_id=s.id,
                                            value=val,
                                            message=f"{s.type.capitalize()} threshold dropped below minimum: {val} < {s.min_threshold}"
                                        )
                                        db.add(new_alert)
                            db.commit()
                            logger.info(f"Ingested telemetry for device {target_device}: T={temp_val}°C H={hum_val}%")

                except Exception as e:
                    logger.error(f"Error polling device {target_device}: {e}")

        except Exception as e:
            logger.error(f"Error querying active sensors in ingestion loop: {e}")
            
        elapsed = time.time() - start_time
        sleep_time = max(0, 60.0 - elapsed)
        await asyncio.sleep(sleep_time) # Exact 1-minute polling intervall

def start_worker():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ingestion_loop())
