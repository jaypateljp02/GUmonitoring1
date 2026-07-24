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
from backend.models.setting import Setting

from backend.services.tapo import get_tapo_telemetry_cached
from backend.services.ewelink import EwelinkClient
from backend.services.whatsapp import send_whatsapp_alert, calculate_priority

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
load_dotenv("C:/GroundUp/ground-up-monitoring/.env")
load_dotenv("C:/GroundUp/ground-up-monitoring/backend/.env")
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
    
    daily_cost = daily_energy * billing_rate
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
    stats.estimated_cost = decimal.Decimal(str(round(daily_cost, 2)))


async def sync_ewelink_devices(db, client: EwelinkClient) -> list:
    """
    Fetch all devices from eWeLink and sync them to rooms/sensors in the database.
    Supports both temperature/humidity sensors (SNZB-02) and power monitoring devices (POWR320D).
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
        
        params = item_data.get("params", {})
        is_temp_hum = EwelinkClient.is_temp_hum_device(params)
        is_power = EwelinkClient.is_power_device(params)

        # Skip devices that are neither temp/hum nor power devices (e.g. Zigbee Bridges)
        if not is_temp_hum and not is_power:
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
            # Do not overwrite room name if it is a power/plug sensor attached to a temperature room
            if is_temp_hum and room.name != device_name:
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

        if is_temp_hum:
            # 2. Find or create Temperature Sensor
            temp_sensor = db.query(Sensor).filter(Sensor.room_id == room_id, Sensor.type == "temperature").first()
            if not temp_sensor:
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

        if is_power:
            # 4. Find or create Plug (power monitoring) Sensor for POWR320D-type devices
            plug_sensor = db.query(Sensor).filter(Sensor.room_id == room_id, Sensor.type == "plug").first()
            if not plug_sensor:
                plug_sensor = db.query(Sensor).filter(Sensor.device_id == device_id, Sensor.type == "plug").first()
                if plug_sensor:
                    plug_sensor.room_id = room_id
                    plug_sensor.active = True
                else:
                    plug_sensor = Sensor(
                        room_id=room_id,
                        name=f"{device_name} Power",
                        type="plug",
                        device_id=device_id,
                        active=True,
                        mock_mode="normal"
                    )
                    db.add(plug_sensor)
                    logger.info(f"Created new plug sensor for eWeLink power device: {device_name} (id: {device_id})")
            else:
                plug_sensor.active = True

    # 4. Do not aggressively deactivate sensors/rooms. 
    # If a device is removed from eWeLink, it will naturally appear as "OFFLINE" 
    # due to the timestamp threshold. Deactivating it hides it completely and 
    # causes UI flapping if the eWeLink API returns a partial list.
        
    db.commit()
    logger.info(f"Sync complete. Synced device IDs: {synced_device_ids}")
    return synced_device_ids

async def ingestion_loop():
    email = os.getenv("EWELINK_EMAIL") or "grounduppune89@gmail.com"
    password = os.getenv("EWELINK_PASSWORD") or "Groundup"
    region = os.getenv("EWELINK_REGION") or "as"

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

            # Sync devices periodically (every ~10 minutes / 2 loops at 5-min interval)
            if use_live and sync_counter >= 2:
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
                sleep_time = max(0, 300.0 - elapsed)
                await asyncio.sleep(sleep_time)
                continue
            
            # Process each active device
            new_offline_alerts = []
            for target_device, sensors in devices_map.items():
                try:
                    if not sensors:
                        continue
                        
                    mode = sensors[0].mock_mode if sensors else "normal"
                    
                    temp_val = None
                    hum_val = None
                    bat_val = 100.0
                    timestamp_parsed = datetime.utcnow()

                    # Power device values (for POWR320D-type eWeLink devices)
                    power_val = None
                    voltage_val = None
                    current_val = None
                    today_energy_val = 0.0
                    month_energy_val = 0.0
                    is_power_device = False

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

                            # Detect if this is a power monitoring device (POWR320D)
                            if EwelinkClient.is_power_device(params) and not EwelinkClient.is_temp_hum_device(params):
                                is_power_device = True
                                sw_state = "off"
                                if "switches" in params and isinstance(params["switches"], list) and len(params["switches"]) > 0:
                                    sw_state = str(params["switches"][0].get("switch", "off")).lower()
                                elif "switch" in params and params["switch"] is not None:
                                    sw_state = str(params["switch"]).lower()

                                raw_power = params.get("power")
                                raw_voltage = params.get("voltage")
                                raw_current = params.get("current")

                                if raw_power is not None:
                                    power_val = float(raw_power)
                                    if power_val > 1000:
                                        power_val = round(power_val / 100.0, 2)

                                if raw_voltage is not None:
                                    voltage_val = float(raw_voltage)
                                    if voltage_val > 1000:
                                        voltage_val = round(voltage_val / 100.0, 1)

                                if raw_current is not None:
                                    c_float = float(raw_current)
                                    if c_float > 1000:
                                        current_val = round(c_float / 1000.0, 2)
                                    elif c_float > 25:
                                        current_val = round(c_float / 100.0, 2)
                                    else:
                                        current_val = round(c_float, 2)

                                if power_val is not None and power_val > 1.0:
                                    sw_state = "on"
                                elif sw_state == "off":
                                    power_val = 0.0
                                    current_val = 0.0

                                # Energy data
                                day_kwh_raw = params.get("dayKwh") if params.get("dayKwh") is not None else (params.get("oneKwh") if params.get("oneKwh") is not None else params.get("todayKwh"))
                                if day_kwh_raw is not None:
                                    today_energy_val = round(float(day_kwh_raw) / 100.0, 3)

                                month_kwh_raw = params.get("monthKwh")
                                if month_kwh_raw is not None:
                                    month_energy_val = round(float(month_kwh_raw) / 100.0, 3)
                                else:
                                    hundred_days = params.get("hundredDaysKwh")
                                    if hundred_days and isinstance(hundred_days, str):
                                        try:
                                            days_to_sum = min(30, len(hundred_days) // 6)
                                            for i in range(days_to_sum):
                                                hex_chunk = hundred_days[i * 6:(i + 1) * 6]
                                                if hex_chunk:
                                                    month_energy_val += int(hex_chunk, 16) / 100.0
                                        except Exception:
                                            pass

                                is_online_in_cloud = device_data.get("online", True)
                                if is_online_in_cloud or power_val is not None:
                                    is_device_reporting = True
                                else:
                                    logger.warning(f"Power device {target_device} is offline in eWeLink cloud.")
                            else:
                                # Standard temp/hum sensor (SNZB-02)
                                raw_temp = params.get("temperature")
                                raw_hum = params.get("humidity")
                                raw_bat = params.get("battery")

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
                                
                                is_online_in_cloud = device_data.get("online", True)
                                if is_online_in_cloud or (temp_val is not None or hum_val is not None):
                                    is_device_reporting = True
                                else:
                                    logger.warning(f"Device {target_device} is offline in eWeLink cloud (online={device_data.get('online')}).")
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
                        ist_offset = timedelta(hours=5, minutes=30)
                        
                        # Find last telemetry timestamp for this device (check both tables)
                        last_telemetry = db.query(DeviceTelemetry).filter(
                            DeviceTelemetry.device_id == target_device
                        ).order_by(DeviceTelemetry.timestamp.desc()).first()

                        last_plug_tel = db.query(PlugTelemetry).filter(
                            PlugTelemetry.device_id == target_device
                        ).order_by(PlugTelemetry.timestamp.desc()).first()

                        # Use whichever was more recent
                        last_ts = None
                        if last_telemetry and last_telemetry.timestamp:
                            last_ts = last_telemetry.timestamp
                        if last_plug_tel and last_plug_tel.timestamp:
                            if last_ts is None or last_plug_tel.timestamp > last_ts:
                                last_ts = last_plug_tel.timestamp

                        if last_ts:
                            last_seen_ist = (last_ts + ist_offset).strftime("%I:%M %p IST (%b %d)")
                            offline_detail = f"went offline at {last_seen_ist}"
                        else:
                            offline_detail = "is offline (stopped sending telemetry data)"

                        for s in sensors:
                            sensor_alerts = alerts_by_sensor.get(s.id, [])
                            has_offline = any("offline" in (a.message or "").lower() for a in sensor_alerts if not a.resolved)
                            if not has_offline:
                                new_alert = Alert(
                                    sensor_id=s.id,
                                    value=Decimal("0.0"),
                                    message=f"[{s.name}] {s.type.capitalize()} sensor {offline_detail}.",
                                    created_at=timestamp_parsed
                                )
                                db.add(new_alert)
                                db.flush()
                                alerts_by_sensor.setdefault(s.id, []).append(new_alert)
                                new_offline_alerts.append(new_alert)
                    else:
                        # Immediately resolve any active offline alerts as soon as device is reporting
                        for s in sensors:
                            sensor_alerts = alerts_by_sensor.get(s.id, [])
                            for alert in sensor_alerts:
                                if "offline" in (alert.message or "").lower() and not alert.resolved:
                                    alert.resolved = True
                                    logger.info(f"Sensor {s.name} (id: {s.id}) is now ONLINE. Resolved offline alert {alert.id}.")

                    # === POWER DEVICE TELEMETRY (POWR320D) ===
                    if is_power_device and power_val is not None:
                        plug_tel = PlugTelemetry(
                            device_id=target_device,
                            apower=power_val,
                            voltage=voltage_val or 0.0,
                            current=current_val or 0.0,
                            today_energy=today_energy_val,
                            month_energy=month_energy_val,
                            timestamp=timestamp_parsed
                        )
                        db.add(plug_tel)

                        # Update tapo_last_seen and tapo_status on the plug sensor
                        for s in sensors:
                            if s.type == "plug":
                                s.tapo_last_seen = timestamp_parsed
                                s.tapo_status = "online"
                                s.tapo_error = None

                        logger.info(f"Queued plug telemetry for eWeLink power device {target_device}: P={power_val}W V={voltage_val}V I={current_val}A")

                    # === TEMPERATURE/HUMIDITY DEVICE TELEMETRY (SNZB-02) ===
                    elif not is_power_device and temp_val is not None and hum_val is not None:
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
                            
                            # Skip plug sensors in temp/hum processing
                            if s.type == "plug":
                                continue
                            
                            # 3. Check thresholds and alert with door open alert suppression
                            sensor_alerts = alerts_by_sensor.get(s.id, [])
                            open_alert = next((a for a in sensor_alerts if not a.resolved and "offline" not in (a.message or "").lower()), None)
                            
                            is_violating = False
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
                                    
                                    # Calculate priority FIRST to determine required duration
                                    room = db.query(Room).filter(Room.id == s.room_id).first()
                                    room_type = room.type if room else "room"
                                    prio = calculate_priority(room_type, s.type, float(val), s)
                                    
                                    # Critical (high-high) = 10 min — important, alert fast
                                    # High / Medium / Low = 60 min (1 hour) — don't spam
                                    required_duration = 10.0 if prio == "Critical" else 60.0
                                    
                                    if violation_duration >= required_duration:
                                        logger.warning(f"THRESHOLD VIOLATION SUSTAINED for sensor {s.id}: {val} (duration {violation_duration:.1f} mins, priority={prio}, threshold={required_duration} mins)")
                                        new_alert = Alert(
                                            sensor_id=s.id,
                                            value=val,
                                            message=f"[{s.name}] {s.type.capitalize()} threshold violated for {violation_duration:.1f} mins: {val}",
                                            created_at=timestamp_parsed
                                        )
                                        db.add(new_alert)
                                        db.flush()
                                        alerts_by_sensor.setdefault(s.id, []).append(new_alert)

                                        # Send WhatsApp Alert
                                        try:
                                            val_str = f"{val}°C" if s.type == "temperature" else f"{val}%"
                                            min_th_str = f"{s.min_threshold}°C" if s.type == "temperature" else f"{s.min_threshold}%"
                                            max_th_str = f"{s.max_threshold}°C" if s.type == "temperature" else f"{s.max_threshold}%"
                                            range_str = f"{min_th_str} - {max_th_str}"

                                            send_whatsapp_alert(
                                                sensor_name=s.name,
                                                alert_type=s.type.capitalize(),
                                                current_value=val_str,
                                                normal_range=range_str,
                                                duration=f"{violation_duration:.1f} mins",
                                                priority=prio,
                                                alert_id=str(new_alert.id)
                                            )
                                        except Exception as wa_err:
                                            logger.error(f"Failed to send threshold violation WhatsApp alert: {wa_err}", exc_info=True)
                                    else:
                                        logger.info(f"Threshold violation suppressed for sensor {s.id}: {val} (duration {violation_duration:.1f} mins < {required_duration} mins, priority={prio})")
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
                if s.type == "plug" and s.device_id:
                    try:
                        update_compressor_stats(db, s, today)
                    except Exception as ex:
                        logger.error(f"Error updating compressor stats for sensor {s.id}: {ex}")

            # Evaluate consolidated offline alerts (check if ALL sensors are offline across DB, or send new offline alerts)
            try:
                total_active_sensors = len(active_sensors)
                current_unresolved_offline_alerts = db.query(Alert).filter(
                    Alert.resolved == False,
                    Alert.message.like("%offline%")
                ).count()
                
                all_sensors_offline = (total_active_sensors > 0 and current_unresolved_offline_alerts >= total_active_sensors)
                
                if all_sensors_offline:
                    # Check cooldown timer for ALL SENSORS OFFLINE alert (send at most once every 2 hours)
                    last_all_offline_setting = db.query(Setting).filter(Setting.key == "last_all_devices_offline_alert").first()
                    now_ts = datetime.utcnow()
                    should_send_all_offline = True
                    if last_all_offline_setting and last_all_offline_setting.value:
                        try:
                            last_sent_dt = datetime.fromisoformat(last_all_offline_setting.value)
                            if (now_ts - last_sent_dt).total_seconds() < 7200:
                                should_send_all_offline = False
                        except Exception:
                            pass
                            
                    if should_send_all_offline:
                        logger.warning("🚨 ALL SENSORS & DEVICES ARE OFFLINE! Sending consolidated WhatsApp alert.")
                        alert_id = str(new_offline_alerts[0].id) if new_offline_alerts else "all_offline"
                        send_whatsapp_alert(
                            sensor_name="ALL SENSORS & EQUIPMENT",
                            alert_type="CRITICAL ALL OFFLINE",
                            current_value="ALL OFFLINE",
                            normal_range="Online",
                            duration="N/A",
                            priority="Critical",
                            alert_id=alert_id
                        )
                        if not last_all_offline_setting:
                            last_all_offline_setting = Setting(
                                key="last_all_devices_offline_alert",
                                value=now_ts.isoformat(),
                                description="Timestamp of last ALL DEVICES OFFLINE alert dispatch"
                            )
                            db.add(last_all_offline_setting)
                        else:
                            last_all_offline_setting.value = now_ts.isoformat()
                        db.flush()
                elif new_offline_alerts:
                    logger.info(f"Processing {len(new_offline_alerts)} new offline alerts in this ingestion cycle.")
                    if len(new_offline_alerts) >= 3:
                        logger.info("Sending a single consolidated offline alert for multiple sensors.")
                        send_whatsapp_alert(
                            sensor_name=f"Multiple ({len(new_offline_alerts)}) Sensors",
                            alert_type="Offline",
                            current_value="OFFLINE",
                            normal_range="Online",
                            duration="N/A",
                            priority="High",
                            alert_id=str(new_offline_alerts[0].id)
                        )
                    else:
                        for alert in new_offline_alerts:
                            s = db.query(Sensor).filter(Sensor.id == alert.sensor_id).first()
                            if s:
                                room = db.query(Room).filter(Room.id == s.room_id).first()
                                room_type = room.type if room else "room"
                                prio = calculate_priority(room_type, "offline", 0.0, s)
                                
                                # Query last seen timestamp
                                last_seen_str = "Offline"
                                if s.device_id:
                                    last_tel = db.query(DeviceTelemetry).filter(
                                        DeviceTelemetry.device_id == s.device_id
                                    ).order_by(DeviceTelemetry.timestamp.desc()).first()
                                    if last_tel and last_tel.timestamp:
                                        last_seen_str = (last_tel.timestamp + timedelta(hours=5, minutes=30)).strftime("%I:%M %p IST")

                                send_whatsapp_alert(
                                    sensor_name=s.name,
                                    alert_type="Offline",
                                    current_value="OFFLINE",
                                    normal_range="Online",
                                    duration=f"Since {last_seen_str}",
                                    priority=prio,
                                    alert_id=str(alert.id)
                                )
                    db.flush()
            except Exception as wa_err:
                logger.error(f"Failed to process offline WhatsApp alerts: {wa_err}", exc_info=True)

            # Commit the entire batch at the end of the loop
            try:
                db.commit()
                logger.info("Successfully committed ingestion cycle telemetry and alerts in a single transaction.")
            except Exception as e:
                logger.error(f"Error committing batch telemetry ingestion: {e}")
                db.rollback()

            # Check and trigger daily report at 10:40 PM IST (22:40 IST)
            try:
                from backend.services.insights import generate_daily_report
                from backend.models.setting import Setting
                
                # Get current date/time in IST
                now_utc = datetime.utcnow()
                now_ist = now_utc + timedelta(hours=5, minutes=30)
                today_str = now_ist.strftime("%Y-%m-%d")
                
                # We target 22:40 (10:40 PM) IST
                target_time = now_ist.replace(hour=22, minute=40, second=0, microsecond=0)
                
                if now_ist >= target_time:
                    # Check if we already sent report for today
                    last_run_setting = db.query(Setting).filter(Setting.key == "last_daily_report_date").first()
                    if not last_run_setting or last_run_setting.value != today_str:
                        logger.info(f"Triggering scheduled daily report for IST date {today_str}...")
                        success = await generate_daily_report(db)
                        if success:
                            logger.info(f"Daily report sent successfully for {today_str}")
                            if not last_run_setting:
                                last_run_setting = Setting(
                                    key="last_daily_report_date",
                                    value=today_str,
                                    description="Date of the last successful daily report run"
                                )
                                db.add(last_run_setting)
                            else:
                                last_run_setting.value = today_str
                            db.commit()
                        else:
                            logger.warning(f"Daily report generation returned False/failed for {today_str}")
            except Exception as cron_err:
                logger.error(f"Error checking/triggering daily report scheduler: {cron_err}", exc_info=True)
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
        sleep_time = max(0, 300.0 - elapsed)
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

if __name__ == "__main__":
    start_worker()
