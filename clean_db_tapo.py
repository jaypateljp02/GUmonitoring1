
import sys
sys.path.append("C:/GroundUp/ground-up-monitoring")
from backend.database import SessionLocal
from backend.models import Sensor

db = SessionLocal()
sensors = db.query(Sensor).all()
count = 0
for s in sensors:
    if s.tapo_ip or s.tapo_mac or s.tapo_username or s.tapo_password:
        print(f"Cleaning legacy Tapo fields from sensor ID: {s.id} ({s.name})")
        s.tapo_ip = None
        s.tapo_mac = None
        s.tapo_username = None
        s.tapo_password = None
        s.tapo_status = None
        count += 1
db.commit()
print(f"Successfully cleaned {count} sensors in DB!")
