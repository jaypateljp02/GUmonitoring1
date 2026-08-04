
import sys
import os
import asyncio
from datetime import datetime, date

sys.path.append("C:/GroundUp/ground-up-monitoring")
from backend.database import SessionLocal
from backend.models import Sensor, DeviceTelemetry
from backend.services.ewelink import EwelinkClient

def run_tests():
    db = SessionLocal()
    
    print("=== 1. PLUG SENSORS IN DB ===")
    plugs = db.query(Sensor).filter(Sensor.type == "plug").all()
    for s in plugs:
        print(f"ID: {s.id} | DevID: {s.device_id} | Name: {s.name} | TapoIP: {s.tapo_ip} | Active: {s.active}")

    print("\n=== 2. ALL SENSORS IN DB (CHECKING FOR OFFLINE / ONLINE MISMATCH) ===")
    all_s = db.query(Sensor).all()
    for s in all_s:
        if "tapo" in (s.name or "").lower() or s.tapo_ip or s.tapo_mac:
            print(f"LEGACY TAPO FOUND -> ID: {s.id} | Name: {s.name} | Type: {s.type} | TapoIP: {s.tapo_ip} | Active: {s.active}")

    print("\n=== 3. TELEMETRY LOGS FOR TODAY ===")
    today = date.today()
    recs = db.query(DeviceTelemetry).filter(DeviceTelemetry.timestamp >= datetime(today.year, today.month, today.day)).order_by(DeviceTelemetry.timestamp.asc()).all()
    print(f"Total telemetry records for today ({today}): {len(recs)}")
    if recs:
        print(f"First reading today (UTC): {recs[0].timestamp} -> IST: {recs[0].timestamp.hour+5}:{recs[0].timestamp.minute+30}")
        print(f"Last reading today (UTC): {recs[-1].timestamp} -> IST: {recs[-1].timestamp.hour+5}:{recs[-1].timestamp.minute+30}")
    else:
        print("NO TELEMETRY RECORDS FOUND FOR TODAY!")

    print("\n=== 4. TESTING EWELINK WEBSOCKET TOGGLE ===")
    async def test_ws():
        email = os.getenv("EWELINK_EMAIL", "grounduppune89@gmail.com")
        pwd = os.getenv("EWELINK_PASSWORD", "Groundup")
        reg = os.getenv("EWELINK_REGION", "as")
        c = EwelinkClient(email, pwd, reg)
        await c.login()
        print(f"Logged in to eWeLink as {email}. Attempting to toggle Black Fridge (1002912a22)...")
        try:
            res = await c.set_device_switch("1002912a22", "off")
            print("Toggle OFF result:", res)
        except Exception as e:
            import traceback
            print("Toggle OFF Exception:", e)
            traceback.print_exc()

    asyncio.run(test_ws())

if __name__ == "__main__":
    run_tests()
