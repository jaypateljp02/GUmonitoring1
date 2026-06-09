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
from backend.models.ewelink_token import EwelinkToken
from backend.services.ewelink import EwelinkClient

load_dotenv()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

async def refresh_user_token(db, token_record):
    """
    Attempt to refresh the expired access token using the refresh token.
    """
    logger.info("Attempting to refresh eWeLink access token...")
    client = EwelinkClient(region=token_record.region)
    new_tokens = await client.refresh_tokens(token_record.refresh_token)
    if new_tokens and new_tokens.get("at") and new_tokens.get("rt"):
        token_record.access_token = new_tokens["at"]
        token_record.refresh_token = new_tokens["rt"]
        db.commit()
        logger.info("eWeLink access token refreshed successfully!")
        return new_tokens["at"]
    else:
        logger.error("Failed to refresh eWeLink access token. Refresh token might be expired.")
        return None

async def ingestion_loop():
    while True:
        start_time = time.time()
        db = SessionLocal()
        try:
            # 1. Fetch active eWeLink token if available
            token_record = db.query(EwelinkToken).filter(EwelinkToken.id == "default").first()
            
            # 2. Fetch all active sensors from the DB
            active_sensors = db.query(Sensor).filter(Sensor.active == True).all()
            
            # Map of device_id -> List[Sensor]
            devices_map = {}
            for s in active_sensors:
                if s.device_id:
                    if s.device_id not in devices_map:
                        devices_map[s.device_id] = []
                    devices_map[s.device_id].append(s)

            # Polling variables
            use_live = False
            thing_list = None
            client = None
            
            if token_record:
                client = EwelinkClient(access_token=token_record.access_token, region=token_record.region)
                # Fetch all devices at once to avoid multiple API calls
                thing_list = await client.get_all_devices()
                
                # Check for token expiration (401/402 or thingList is None)
                if thing_list is None:
                    # Try to refresh token
                    new_at = await refresh_user_token(db, token_record)
                    if new_at:
                        client.access_token = new_at
                        thing_list = await client.get_all_devices()
                
                if thing_list is not None:
                    use_live = True
                    logger.info("Successfully fetched live telemetry from eWeLink cloud.")
                else:
                    logger.warning("eWeLink token is invalid or expired. Falling back to simulator mode.")
            else:
                logger.info("No eWeLink account linked. Running in simulator mode.")

            # Process each active device
            for target_device, sensors in devices_map.items():
                try:
                    mode = sensors[0].mock_mode if sensors else "normal"
                    if mode == "failover":
                        logger.info(f"Simulator in failover mode for device {target_device}. No data ingested.")
                        continue

                    temp_val = None
                    hum_val = None
                    bat_val = 100.0
                    timestamp_parsed = datetime.utcnow()

                    # Find device telemetry in thingList if using live mode
                    device_data = None
                    if use_live and thing_list:
                        for t in thing_list:
                            item_data = t.get("itemData", {})
                            if item_data.get("deviceid") == target_device:
                                device_data = item_data
                                break

                    if use_live and device_data:
                        params = device_data.get("params", {})
                        raw_temp = params.get("temperature")
                        raw_hum = params.get("humidity")
                        raw_bat = params.get("battery")

                        # Clean values
                        if raw_temp is not None:
                            temp_val = float(raw_temp)
                            if temp_val > 100 or temp_val < -100:
                                temp_val = temp_val / 100.0
                        if raw_hum is not None:
                            hum_val = float(raw_hum)
                            if hum_val > 100:
                                hum_val = hum_val / 100.0
                        if raw_bat is not None:
                            bat_val = float(raw_bat)
                    else:
                        # Fallback: Simulator mode logic
                        if mode == "ice":
                            temp_val = round(random.uniform(-5.0, -2.0), 2)
                        elif mode == "warm":
                            temp_val = round(random.uniform(5.0, 10.0), 2)
                        else:
                            temp_val = round(random.uniform(2.0, 3.5), 2)
                        
                        hum_val = round(random.uniform(40.0, 60.0), 2)
                        bat_val = 98.0

                    if temp_val is not None and hum_val is not None:
                        # 1. Raw Telemetry
                        telemetry = DeviceTelemetry(
                            device_id=target_device,
                            temperature=temp_val,
                            humidity=hum_val,
                            battery_level=bat_val,
                            timestamp=timestamp_parsed
                        )
                        db.add(telemetry)
                        
                        # 2. Map to logical sensors
                        for s in sensors:
                            val = temp_val if s.type == "temperature" else hum_val
                            reading = SensorReading(
                                sensor_id=s.id,
                                value=val,
                                timestamp=timestamp_parsed
                            )
                            db.add(reading)
                            
                            # 3. Check thresholds and alert
                            if s.max_threshold is not None and val > s.max_threshold:
                                recent_alert = db.query(Alert).filter(Alert.sensor_id == s.id, Alert.resolved == False).first()
                                if not recent_alert:
                                    logger.warning(f"THRESHOLD EXCEEDED for sensor {s.id}: {val} > {s.max_threshold}")
                                    new_alert = Alert(
                                        sensor_id=s.id,
                                        value=val,
                                        message=f"{s.type.capitalize()} threshold exceeded: {val} > {s.max_threshold}",
                                        timestamp=timestamp_parsed
                                    )
                                    db.add(new_alert)
                            
                            if s.min_threshold is not None and val < s.min_threshold:
                                recent_alert = db.query(Alert).filter(Alert.sensor_id == s.id, Alert.resolved == False).first()
                                if not recent_alert:
                                    logger.warning(f"THRESHOLD DROPPED for sensor {s.id}: {val} < {s.min_threshold}")
                                    new_alert = Alert(
                                        sensor_id=s.id,
                                        value=val,
                                        message=f"{s.type.capitalize()} threshold dropped below minimum: {val} < {s.min_threshold}",
                                        timestamp=timestamp_parsed
                                    )
                                    db.add(new_alert)
                        db.commit()
                        logger.info(f"Ingested telemetry for device {target_device}: T={temp_val}°C H={hum_val}%")
                except Exception as e:
                    logger.error(f"Error polling device {target_device}: {e}")
                    db.rollback()

        except Exception as e:
            logger.error(f"Error in ingestion loop: {e}")
        finally:
            db.close()
            
        elapsed = time.time() - start_time
        sleep_time = max(0, 60.0 - elapsed)
        await asyncio.sleep(sleep_time)

def start_worker():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ingestion_loop())
