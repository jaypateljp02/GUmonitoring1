
import sys
import os
import asyncio
import json

sys.path.append("C:/GroundUp/ground-up-monitoring")
from backend.services.ewelink import EwelinkClient
from backend.database import SessionLocal
from backend.models.sensor import Sensor

async def full_audit():
    email = os.getenv("EWELINK_EMAIL", "grounduppune89@gmail.com")
    pwd = os.getenv("EWELINK_PASSWORD", "Groundup")
    reg = os.getenv("EWELINK_REGION", "as")
    
    c = EwelinkClient(email, pwd, reg)
    await c.login()
    
    thing_list = await c.get_all_devices()
    
    db = SessionLocal()
    all_sensors = db.query(Sensor).filter(Sensor.active == True).all()
    
    # Map DB sensors by device_id
    db_sensors = {}
    for s in all_sensors:
        if s.device_id not in db_sensors:
            db_sensors[s.device_id] = []
        db_sensors[s.device_id].append(s)
    
    print("=" * 100)
    print("SECTION 1: ALL EWELINK DEVICES (name, deviceid, online, battery, type)")
    print("=" * 100)
    
    ewelink_devices = {}
    if thing_list:
        for t in thing_list:
            item = t.get("itemData", {})
            did = item.get("deviceid", "")
            name = item.get("name", "UNNAMED")
            online = item.get("online", False)
            params = item.get("params", {})
            battery = params.get("battery")
            model = item.get("productModel", "")
            
            # Detect type
            is_power = "power" in params or "voltage" in params or "current" in params
            is_temp = "temperature" in params or "humidity" in params
            
            device_type = "unknown"
            if is_power and not is_temp:
                device_type = "PLUG (POWR320D)"
            elif is_temp:
                device_type = "TEMP/HUM (SNZB-02)"
            
            # Check switch state for plugs
            sw_state = None
            if is_power:
                if "switches" in params and isinstance(params["switches"], list) and len(params["switches"]) > 0:
                    sw_state = params["switches"][0].get("switch", "off")
                elif "switch" in params:
                    sw_state = params["switch"]
            
            ewelink_devices[did] = {
                "name": name,
                "online": online,
                "battery": battery,
                "type": device_type,
                "model": model,
                "sw_state": sw_state
            }
            
            in_db = "YES" if did in db_sensors else "NO"
            db_name = db_sensors[did][0].name if did in db_sensors else "N/A"
            db_types = [s.type for s in db_sensors.get(did, [])]
            
            print(f"eWeLink: {name:30s} | ID: {did:12s} | Online: {str(online):5s} | Battery: {str(battery):4s} | Type: {device_type:20s} | Switch: {str(sw_state):4s} | InDB: {in_db} | DB_Name: {db_name} | DB_Types: {db_types}")
    
    print()
    print("=" * 100)
    print("SECTION 2: DB SENSORS NOT FOUND IN EWELINK")
    print("=" * 100)
    for did, sensors in db_sensors.items():
        if did not in ewelink_devices:
            for s in sensors:
                print(f"DB ONLY: {s.name:30s} | ID: {did:12s} | Type: {s.type:12s} | Room: {s.room_id}")
    
    print()
    print("=" * 100)
    print("SECTION 3: EWELINK DEVICES NOT IN DB")
    print("=" * 100)
    for did, info in ewelink_devices.items():
        if did not in db_sensors:
            print(f"EWELINK ONLY: {info['name']:30s} | ID: {did:12s} | Online: {info['online']} | Type: {info['type']}")
    
    print()
    print("=" * 100)
    print("SECTION 4: NAME MISMATCHES (DB name vs eWeLink name)")
    print("=" * 100)
    for did, info in ewelink_devices.items():
        if did in db_sensors:
            for s in db_sensors[did]:
                if s.name != info['name'] and s.type == 'plug':
                    print(f"MISMATCH: DB='{s.name}' vs eWeLink='{info['name']}' | ID: {did}")
    
    db.close()

asyncio.run(full_audit())
