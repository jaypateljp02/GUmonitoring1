"""Update tapo_ip for sensors in the database."""
import sys
sys.path.insert(0, ".")
from backend.database import SessionLocal
from backend.models.sensor import Sensor

db = SessionLocal()

updates = {
    # device_id -> new tapo_ip
    "a4b0028d6e": "192.168.0.100",   # Hall fridge and freezer (10)
    "a4b002898f": "192.168.0.101",   # Miso room
    "a4b002884e": "192.168.0.109",   # Black Fridge
}

for device_id, new_ip in updates.items():
    sensors = db.query(Sensor).filter(Sensor.device_id == device_id).all()
    for s in sensors:
        old_ip = s.tapo_ip
        s.tapo_ip = new_ip
        print(f"  {s.name:35s} | {s.type:12s} | tapo_ip: {old_ip} -> {new_ip}")

db.commit()
print("\nDone! All tapo_ip values updated.")
db.close()
