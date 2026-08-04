import sys
sys.path.append('d:/Ground Up/ground-up-monitoring')
from backend.database import SessionLocal
from backend.models.sensor import Sensor
from sqlalchemy import text

db = SessionLocal()

# Get all temperature/humidity sensor device_ids
temp_hum_sensors = db.query(Sensor.device_id).filter(Sensor.type.in_(['temperature', 'humidity'])).all()
temp_hum_ids = set([s[0] for s in temp_hum_sensors])

print('Found temp/hum IDs:', temp_hum_ids)

# Check if any are in plug_telemetry
rows = db.execute(text('SELECT DISTINCT device_id FROM monitoring.plug_telemetry')).fetchall()
plug_tel_ids = [r[0] for r in rows]

overlap = temp_hum_ids.intersection(plug_tel_ids)
print('Bogus IDs in plug_telemetry:', overlap)

for dev_id in overlap:
    db.execute(text(f"DELETE FROM monitoring.plug_telemetry WHERE device_id = '{dev_id}'"))
    print(f'Deleted bogus plug_telemetry for {dev_id}')

db.commit()
print('Cleanup complete!')
