import os
import time
import random
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv

from api.database import SessionLocal
from api.models.device_telemetry import DeviceTelemetry
from api.models.sensor import Sensor
from api.models.reading import SensorReading
from api.models.alert import Alert
from api.services.ewelink import EwelinkClient

load_dotenv()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

class WorkerState:
    MOCK_STATE = "normal"  # 'normal', 'ice', 'warm', 'failover'

async def ingestion_loop():
    email = os.getenv("EWELINK_EMAIL")
    password = os.getenv("EWELINK_PASSWORD")
    target_device = os.getenv("EWELINK_SENSOR_ID", "a4b002884e")
    region = os.getenv("EWELINK_REGION", "as")
    
    client = None
    use_live = False
    
    if email and password:
        logger.info("Initializing eWeLink client...")
        client = EwelinkClient(email=email, password=password, region=region)
        use_live = True
    else:
        logger.warning("No EWELINK_EMAIL in .env. Starting Simulator Mode.")

    while True:
        try:
            if WorkerState.MOCK_STATE == "failover":
                logger.info("Simulator in failover mode. No data ingested.")
                await asyncio.sleep(10)
                continue

            temp_val = None
            hum_val = None
            bat_val = 100.0

            if use_live and WorkerState.MOCK_STATE == "normal":
                status = await client.get_device_status(target_device)
                if status:
                    temp_val = status.get("temperature")
                    hum_val = status.get("humidity")
                    bat_val = status.get("battery", 100.0)
            else:
                # Simulator mode logic
                if WorkerState.MOCK_STATE == "ice":
                    temp_val = round(random.uniform(-5.0, -2.0), 2)
                elif WorkerState.MOCK_STATE == "warm":
                    temp_val = round(random.uniform(5.0, 10.0), 2)
                else: # normal mode (fallback to mock if no live credentials)
                    temp_val = round(random.uniform(2.0, 3.5), 2)
                
                hum_val = round(random.uniform(40.0, 60.0), 2)
                bat_val = 98.0

            if temp_val is not None and hum_val is not None:
                # Save to database
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
                    sensors = db.query(Sensor).filter(Sensor.device_id == target_device, Sensor.active == True).all()
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
                    logger.info(f"Ingested telemetry for {target_device}: T={temp_val}°C H={hum_val}%")

        except Exception as e:
            logger.error(f"Error in ingestion loop: {e}")
            
        await asyncio.sleep(60) # 1-minute polling interval

def start_worker():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ingestion_loop())
