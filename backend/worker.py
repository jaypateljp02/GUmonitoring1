import os
import time
import random
import asyncio
import logging
from datetime import datetime, timedelta, date
from dotenv import load_dotenv
import decimal

from backend.database import SessionLocal
from backend.models.room import Room
from backend.models.device_telemetry import DeviceTelemetry
from backend.models.sensor import Sensor
from backend.models.reading import SensorReading
from backend.models.alert import Alert
from backend.models.plug_telemetry import PlugTelemetry
from backend.models.compressor_stats import CompressorStats
from backend.models.door_event import DoorEvent
from backend.services.tapo import get_tapo_telemetry_cached
from backend.services.ewelink import EwelinkClient

load_dotenv()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

def update_compressor_stats(db, sensor, target_date):
    from backend.models.plug_telemetry import PlugTelemetry
    from backend.models.compressor_stats import CompressorStats
    from datetime import datetime, time
    import decimal

    start_datetime = datetime.combine(target_date, time.min)
    end_datetime = datetime.combine(target_date, time.max)

    # Fetch all telemetry for this device on target_date sorted by timestamp
    records = db.query(PlugTelemetry).filter(
        PlugTelemetry.device_id == sensor.device_id,
        PlugTelemetry.timestamp >= start_datetime,
        PlugTelemetry.timestamp <= end_datetime
    ).order_by(PlugTelemetry.timestamp.asc()).all()

    if not records:
        return

    running_threshold = float(sensor.tapo_running_threshold or 80.0)
    billing_rate = float(sensor.tapo_billing_rate or 17.0)

    cycle_count = 0
    total_runtime_minutes = 0.0
    is_on = False
    
    last_timestamp = None

    for r in records:
        power = float(r.apower)
        current_time = r.timestamp

        if power >= running_threshold:
            if not is_on:
                is_on = True
                cycle_count += 1
            elif last_timestamp is not None:
                delta_mins = (current_time - last_timestamp).total_seconds() / 60.0
                if delta_mins < 15.0:
                    total_runtime_minutes += delta_mins
        else:
            is_on = False

        last_timestamp = current_time

    latest_record = records[-1]
    daily_energy = float(latest_record.today_energy) if latest_record.today_energy is not None else 0.0
    monthly_energy = float(latest_record.month_energy) if latest_record.month_energy is not None else 0.0
    
    # Standardize to kWh
    if daily_energy > 500.0:
        daily_energy = daily_energy / 1000.0
    if monthly_energy > 500.0:
        monthly_energy = monthly_energy / 1000.0

    estimated_cost = monthly_energy * billing_rate
    avg_runtime = total_runtime_minutes / cycle_count if cycle_count > 0 else 0.0

    stats = db.query(CompressorStats).filter(
        CompressorStats.sensor_id == sensor.id,
        CompressorStats.date == target_date
    ).first()

    if not stats:
        stats = CompressorStats(
            sensor_id=sensor.id,
            date=target_date
        )
        db.add(stats)

    stats.cycle_count = cycle_count
    stats.total_runtime_minutes = decimal.Decimal(str(round(total_runtime_minutes, 2)))
    stats.avg_runtime_per_cycle_minutes = decimal.Decimal(str(round(avg_runtime, 2)))
    stats.daily_energy_kwh = decimal.Decimal(str(round(daily_energy, 3)))
    stats.monthly_energy_kwh = decimal.Decimal(str(round(monthly_energy, 3)))
    stats.estimated_cost = decimal.Decimal(str(round(estimated_cost, 2)))


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
        
        # Filter out devices (like Zigbee Bridges) that do not report temperature or humidity telemetry
        params = item_data.get("params", {})
        if "temperature" not in params and "humidity" not in params:
            logger.info(f"Skipping non-telemetry eWeLink device: {item_data.get('name')} (id: {device_id})")
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

    # 4. Do not aggressively deactivate sensors/rooms. 
    # If a device is removed from eWeLink, it will naturally appear as "OFFLINE" 
    # due to the timestamp threshold. Deactivating it hides it completely and 
    # causes UI flapping if the eWeLink API returns a partial list.
        
    db.commit()
    logger.info(f"Sync complete. Synced device IDs: {synced_device_ids}")
    return synced_device_ids

async def ingestion_loop():
    email = os.getenv("EWELINK_EMAIL")
    password = os.getenv("EWELINK_PASSWORD")
    region = os.getenv("EWELINK_REGION", "as")

    client = None
    use_live = False
    has_credentials = bool(email and password)

    if has_credentials:
        logger.info(f"Initializing official eWeLink client for {email}...")
        client = EwelinkClient(email=email, password=password, region=region)
        
        login_success = False
        try:
            login_success = await asyncio.wait_for(client.login(), timeout=10.0)
        except Exception as e:
            logger.error(f"eWeLink login timed out or failed: {e}")
            
        if login_success:
            use_live = True
            # Sync devices immediately on login success
            db = SessionLocal()
            try:
                await asyncio.wait_for(sync_ewelink_devices(db, client), timeout=20.0)
            except Exception as e:
                logger.error(f"Failed to sync devices on startup: {e}")
            finally:
                db.close()
        else:
            logger.error("Failed to authenticate with eWeLink initially. Will retry in loop.")
    else:
        logger.warning("No eWeLink credentials in .env. Running in Simulator Mode.")

    sync_counter = 0

    while True:
        start_time = time.time()
        db = SessionLocal()
        try:
            # Reconnect if credentials are set but login failed/disconnected
            if has_credentials and not use_live:
                logger.info("Attempting to reconnect / login to eWeLink cloud...")
                login_success = False
                try:
                    login_success = await asyncio.wait_for(client.login(), timeout=10.0)
                except Exception as e:
                    logger.error(f"eWeLink login reconnect timed out or failed: {e}")
                
                if login_success:
                    use_live = True
                    try:
                        await asyncio.wait_for(sync_ewelink_devices(db, client), timeout=20.0)
                    except Exception as e:
                        logger.error(f"Failed to sync devices on reconnect: {e}")

            # Sync devices periodically (every 10 minutes / 10 loops)
            if use_live and sync_counter >= 10:
                try:
                    await asyncio.wait_for(sync_ewelink_devices(db, client), timeout=20.0)
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

            # Pre-load all active/unresolved alerts to avoid N+1 queries in the loop
            unresolved_alerts = db.query(Alert).filter(Alert.resolved == False).all()
            alerts_by_sensor = {}
            for a in unresolved_alerts:
                alerts_by_sensor.setdefault(a.sensor_id, []).append(a)

            thing_list = None
            if use_live:
                try:
                    thing_list = await asyncio.wait_for(client.get_all_devices(), timeout=15.0)
                    if thing_list is None:
                        logger.warning("Failed to fetch devices. Retrying login...")
                        login_success = False
                        try:
                            login_success = await asyncio.wait_for(client.login(), timeout=10.0)
                        except Exception:
                            pass
                        if login_success:
                            thing_list = await asyncio.wait_for(client.get_all_devices(), timeout=15.0)
                except Exception as e:
                    logger.error(f"Failed to fetch devices from eWeLink API (Timeout/Error): {e}")
                    thing_list = None

            # If we have credentials but the API/login failed to fetch devices,
            # we should skip this loop cycle rather than writing fake/simulator data.
            if has_credentials and (not use_live or thing_list is None):
                logger.warning("Skipping device processing this cycle due to eWeLink API/login failure.")
                sync_counter += 1
                elapsed = time.time() - start_time
                sleep_time = max(0, 60.0 - elapsed)
                await asyncio.sleep(sleep_time)
                continue
            
            # Process each active device
            for target_device, sensors in devices_map.items():
                try:
                    if not sensors:
                        continue
                        
                    mode = sensors[0].mock_mode if sensors else "normal"
                    
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

                    is_device_reporting = False
                    if mode == "failover":
                        is_device_reporting = False
                    elif use_live:
                        if device_data:
                            params = device_data.get("params", {})
                            raw_temp = params.get("temperature")
                            raw_hum = params.get("humidity")
                            raw_bat = params.get("battery")
                            trig_time = params.get("trigTime")

                            is_online_by_trig = True
                            if trig_time:
                                try:
                                    trig_ts = float(trig_time) / 1000.0
                                    if time.time() - trig_ts > 24 * 3600:
                                        is_online_by_trig = False
                                        logger.warning(f"Device {target_device} trigTime is stale (older than 24h): {datetime.utcfromtimestamp(trig_ts)}")
                                except Exception as ex:
                                    logger.error(f"Error parsing trigTime for device {target_device}: {ex}")

                            is_online_in_cloud = device_data.get("online", True) if not trig_time else is_online_by_trig
                            if is_online_in_cloud:
                                if raw_temp is not None:
                                    temp_val = float(raw_temp)
                                    is_int = isinstance(raw_temp, int) or (isinstance(raw_temp, str) and "." not in raw_temp)
                                    if is_int or temp_val > 100 or temp_val < -100:
                                        temp_val = temp_val / 100.0
                                if raw_hum is not None:
                                    hum_val = float(raw_hum)
                                    is_int = isinstance(raw_hum, int) or (isinstance(raw_hum, str) and "." not in raw_hum)
                                    if is_int or hum_val > 100:
                                        hum_val = hum_val / 100.0
                                if raw_bat is not None:
                                    bat_val = float(raw_bat)
                                
                                if temp_val is not None or hum_val is not None:
                                    is_device_reporting = True
                            else:
                                logger.warning(f"Device {target_device} is offline (cloud online={device_data.get('online')}, trigTime={trig_time}).")
                    else:
                        if mode == "ice":
                            temp_val = round(random.uniform(-5.0, -2.0), 2)
                        elif mode == "warm":
                            temp_val = round(random.uniform(5.0, 10.0), 2)
                        else:
                            temp_val = round(random.uniform(2.0, 3.5), 2)
                        
                        hum_val = round(random.uniform(40.0, 60.0), 2)
                        bat_val = 98.0
                        is_device_reporting = True

                    # Handle database alerts for offline status
                    if not is_device_reporting:
                        logger.warning(f"Device {target_device} is offline or not reporting. Queueing offline alerts.")
                        from decimal import Decimal
                        for s in sensors:
                            # Check if active offline alert exists in pre-loaded alerts
                            sensor_alerts = alerts_by_sensor.get(s.id, [])
                            has_offline = any("offline" in (a.message or "").lower() for a in sensor_alerts)
                            if not has_offline:
                                new_alert = Alert(
                                    sensor_id=s.id,
                                    value=Decimal("0.0"),
                                    message=f"[{s.name}] {s.type.capitalize()} sensor is offline: Device {target_device} stopped sending data.",
                                    created_at=timestamp_parsed
                                )
                                db.add(new_alert)
                                alerts_by_sensor.setdefault(s.id, []).append(new_alert)
                    else:
                        # Resolve active offline alerts
                        for s in sensors:
                            sensor_alerts = alerts_by_sensor.get(s.id, [])
                            for alert in sensor_alerts:
                                if "offline" in (alert.message or "").lower() and not alert.resolved:
                                    alert.resolved = True

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
                            
                            # 3. Check thresholds and alert with door open alert suppression
                            sensor_alerts = alerts_by_sensor.get(s.id, [])
                            open_alert = next((a for a in sensor_alerts if not a.resolved and "offline" not in (a.message or "").lower()), None)
                            
                            is_violating = False
                            is_alert_enabled = not (s.min_threshold == 0 and s.max_threshold == 0)
                            if is_alert_enabled:
                                if s.max_threshold is not None and val > s.max_threshold:
                                    is_violating = True
                                if s.min_threshold is not None and val < s.min_threshold:
                                    is_violating = True
                            
                            if open_alert:
                                if not is_violating:
                                    # Recovered from threshold violation
                                    open_alert.resolved = True
                                    logger.info(f"Alert resolved for sensor {s.id}: value {val} returned within limits.")
                            else:
                                if is_violating:
                                    # Find when this violation sequence started
                                    recent_readings = db.query(SensorReading).filter(
                                        SensorReading.sensor_id == s.id
                                    ).order_by(SensorReading.recorded_at.desc()).limit(30).all()
                                    
                                    first_violated_at = timestamp_parsed
                                    for r in recent_readings:
                                        r_violating = False
                                        if s.max_threshold is not None and r.value > s.max_threshold:
                                            r_violating = True
                                        if s.min_threshold is not None and r.value < s.min_threshold:
                                            r_violating = True
                                        
                                        if r_violating:
                                            first_violated_at = r.recorded_at
                                        else:
                                            break
                                            
                                    violation_duration = (timestamp_parsed - first_violated_at).total_seconds() / 60.0
                                    if violation_duration >= 10.0:
                                        logger.warning(f"THRESHOLD VIOLATION SUSTAINED for sensor {s.id}: {val} (duration {violation_duration:.1f} mins)")
                                        new_alert = Alert(
                                            sensor_id=s.id,
                                            value=val,
                                            message=f"[{s.name}] {s.type.capitalize()} threshold violated for {violation_duration:.1f} mins: {val}",
                                            created_at=timestamp_parsed
                                        )
                                        db.add(new_alert)
                                        alerts_by_sensor.setdefault(s.id, []).append(new_alert)
                                    else:
                                        logger.info(f"Threshold violation suppressed for sensor {s.id}: {val} (duration {violation_duration:.1f} mins < 10)")
                                else:
                                    # Normal value, check if previous reading was violating to log door open event
                                    recent_readings = db.query(SensorReading).filter(
                                        SensorReading.sensor_id == s.id
                                    ).order_by(SensorReading.recorded_at.desc()).limit(30).all()
                                    
                                    if recent_readings:
                                        prev_r = recent_readings[0]
                                        prev_violating = False
                                        if s.max_threshold is not None and prev_r.value > s.max_threshold:
                                            prev_violating = True
                                        if s.min_threshold is not None and prev_r.value < s.min_threshold:
                                            prev_violating = True
                                            
                                        if prev_violating:
                                            # Tracing back to find when violation sequence started
                                            opened_at = timestamp_parsed
                                            for r in recent_readings:
                                                r_violating = False
                                                if s.max_threshold is not None and r.value > s.max_threshold:
                                                    r_violating = True
                                                if s.min_threshold is not None and r.value < s.min_threshold:
                                                    r_violating = True
                                                
                                                if r_violating:
                                                    opened_at = r.recorded_at
                                                else:
                                                    break
                                            
                                            duration = (timestamp_parsed - opened_at).total_seconds() / 60.0
                                            if duration > 0:
                                                door_event = DoorEvent(
                                                    sensor_id=s.id,
                                                    opened_at=opened_at,
                                                    closed_at=timestamp_parsed,
                                                    duration_minutes=decimal.Decimal(str(round(duration, 2)))
                                                )
                                                db.add(door_event)
                                                logger.info(f"Suppressed alert: door event logged for sensor {s.id} (duration {duration:.1f} mins)")
                                                
                            reading = SensorReading(
                                sensor_id=s.id,
                                value=val,
                                recorded_at=timestamp_parsed
                            )
                            db.add(reading)
                        logger.info(f"Queued telemetry ingestion for device {target_device}: T={temp_val}°C H={hum_val}%")
                except Exception as e:
                    logger.error(f"Error processing device {target_device}: {e}")
            
            # Recalculate compressor stats for all sensors with plug telemetry
            today = datetime.utcnow().date()
            for s in active_sensors:
                if s.type == "temperature" and s.device_id:
                    try:
                        update_compressor_stats(db, s, today)
                    except Exception as ex:
                        logger.error(f"Error updating compressor stats for sensor {s.id}: {ex}")

            # Commit the entire batch at the end of the loop
            try:
                db.commit()
                logger.info("Successfully committed ingestion cycle telemetry and alerts in a single transaction.")
            except Exception as e:
                logger.error(f"Error committing batch telemetry ingestion: {e}")
                db.rollback()
        except Exception as e:
            logger.error(f"Error in ingestion loop: {e}")
            try:
                alert = Alert(
                    message=f"Worker Loop Crashed: {str(e)[:200]}",
                    severity="critical"
                )
                db.add(alert)
                db.commit()
            except:
                pass
            db.rollback()
        finally:
            db.close()
            
        sync_counter += 1
        elapsed = time.time() - start_time
        sleep_time = max(0, 60.0 - elapsed)
        await asyncio.sleep(sleep_time)

def start_worker():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(ingestion_loop())
    except Exception as e:
        logger.error(f"Worker completely crashed: {e}")
        from backend.database import SessionLocal
        from backend.models import Alert
        try:
            db = SessionLocal()
            alert = Alert(message=f"FATAL WORKER CRASH: {str(e)[:200]}", severity="critical")
            db.add(alert)
            db.commit()
        except:
            pass
