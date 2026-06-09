import os
import time
import random
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv

from backend.database import SessionLocal
from backend.models.room import Room
from backend.models.device_telemetry import DeviceTelemetry
from backend.models.sensor import Sensor
from backend.models.reading import SensorReading
from backend.models.alert import Alert
from backend.services.ewelink import EwelinkClient

load_dotenv()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

async def sync_ewelink_devices(db, client: EwelinkClient) -> list:
    """
    Fetch all devices from eWeLink and sync them to rooms/sensors in the database.
    Deactivates any mock/unlinked sensors.
    """
    logger.info("Synchronizing eWeLink devices with database...")
    thing_list = await client.get_all_devices()
    if thing_list is None:
        logger.error("Failed to retrieve eWeLink devices during sync.")
        return []

    synced_device_ids = []
    
    for t in thing_list:
        item_data = t.get("itemData", {})
        device_id = item_data.get("deviceid")
        if not device_id:
            continue
        
        device_name = item_data.get("name") or f"Device {device_id[:6]}"
        synced_device_ids.append(device_id)

        # 1. Find or create Room
        existing_sensor = db.query(Sensor).filter(Sensor.device_id == device_id).first()
        room = None
        if existing_sensor and existing_sensor.room_id:
            room = db.query(Room).filter(Room.id == existing_sensor.room_id).first()

        if room:
            if room.name != device_name:
                room.name = device_name
            room.active = True
            room_id = room.id
        else:
            # Determine room type
            room_type = "room"
            name_lower = device_name.lower()
            if "fridge" in name_lower:
                room_type = "fridge"
            elif "freezer" in name_lower:
                room_type = "freezer"
                
            # Check if a room with the same name already exists
            room = db.query(Room).filter(Room.name == device_name).first()
            if not room:
                room = Room(
                    name=device_name,
                    type=room_type,
                    active=True
                )
                db.add(room)
                db.flush() # Populate room.id
            else:
                room.active = True
                room.type = room_type
                
            room_id = room.id
            
            # Update room_id for any existing sensors with this device_id
            if existing_sensor:
                db.query(Sensor).filter(Sensor.device_id == device_id).update({"room_id": room_id}, synchronize_session=False)

        # 2. Find or create Temperature Sensor
        temp_sensor = db.query(Sensor).filter(Sensor.room_id == room_id, Sensor.type == "temperature").first()
        if not temp_sensor:
            # Check if there's a temp sensor with device_id but null room_id
            temp_sensor = db.query(Sensor).filter(Sensor.device_id == device_id, Sensor.type == "temperature").first()
            if temp_sensor:
                temp_sensor.room_id = room_id
                temp_sensor.active = True
            else:
                temp_sensor = Sensor(
                    room_id=room_id,
                    name=f"{device_name} Temp",
                    type="temperature",
                    device_id=device_id,
                    min_threshold=2.0 if room.type in ["fridge", "freezer"] else 18.0,
                    max_threshold=6.0 if room.type == "fridge" else (-5.0 if room.type == "freezer" else 28.0),
                    active=True
                )
                db.add(temp_sensor)
        else:
            temp_sensor.active = True

        # 3. Find or create Humidity Sensor
        hum_sensor = db.query(Sensor).filter(Sensor.room_id == room_id, Sensor.type == "humidity").first()
        if not hum_sensor:
            # Check if there's a hum sensor with device_id but null room_id
            hum_sensor = db.query(Sensor).filter(Sensor.device_id == device_id, Sensor.type == "humidity").first()
            if hum_sensor:
                hum_sensor.room_id = room_id
                hum_sensor.active = True
            else:
                hum_sensor = Sensor(
                    room_id=room_id,
                    name=f"{device_name} Hum",
                    type="humidity",
                    device_id=device_id,
                    min_threshold=40.0,
                    max_threshold=65.0,
                    active=True
                )
                db.add(hum_sensor)
        else:
            hum_sensor.active = True

    # 4. Deactivate mock/removed sensors and clean up inactive rooms
    if synced_device_ids:
        # Deactivate sensors not in eWeLink list
        db.query(Sensor).filter(Sensor.device_id.notin_(synced_device_ids)).update({"active": False}, synchronize_session=False)
        
        # Deactivate rooms that have no active sensors
        active_room_ids = db.query(Sensor.room_id).filter(Sensor.active == True).distinct().all()
        active_room_ids = [r[0] for r in active_room_ids if r[0]]
        
        db.query(Room).filter(Room.id.notin_(active_room_ids)).update({"active": False}, synchronize_session=False)
        
    db.commit()
    logger.info(f"Sync complete. Synced device IDs: {synced_device_ids}")
    return synced_device_ids

async def ingestion_loop():
    email = os.getenv("EWELINK_EMAIL")
    password = os.getenv("EWELINK_PASSWORD")
    region = os.getenv("EWELINK_REGION", "as")

    client = None
    use_live = False

    if email and password:
        logger.info(f"Initializing official eWeLink client for {email}...")
        client = EwelinkClient(email=email, password=password, region=region)
        if await client.login():
            use_live = True
            # Sync devices immediately on login success
            db = SessionLocal()
            try:
                await sync_ewelink_devices(db, client)
            except Exception as e:
                logger.error(f"Failed to sync devices on startup: {e}")
            finally:
                db.close()
        else:
            logger.error("Failed to authenticate with eWeLink. Running in Simulator Mode.")
    else:
        logger.warning("No eWeLink credentials in .env. Running in Simulator Mode.")

    sync_counter = 0

    while True:
        start_time = time.time()
        db = SessionLocal()
        try:
            # Sync devices periodically (every 10 minutes / 10 loops)
            if use_live and sync_counter >= 10:
                try:
                    await sync_ewelink_devices(db, client)
                    sync_counter = 0
                except Exception as e:
                    logger.error(f"Failed to sync devices: {e}")
            
            # Fetch all active sensors from the DB
            active_sensors = db.query(Sensor).filter(Sensor.active == True).all()
            
            # Map of device_id -> List[Sensor]
            devices_map = {}
            for s in active_sensors:
                if s.device_id:
                    if s.device_id not in devices_map:
                        devices_map[s.device_id] = []
                    devices_map[s.device_id].append(s)

            thing_list = None
            if use_live:
                thing_list = await client.get_all_devices()
                if thing_list is None:
                    logger.warning("Failed to fetch devices. Retrying login...")
                    if await client.login():
                        thing_list = await client.get_all_devices()
            
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
            
        sync_counter += 1
        elapsed = time.time() - start_time
        sleep_time = max(0, 60.0 - elapsed)
        await asyncio.sleep(sleep_time)

def start_worker():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ingestion_loop())
