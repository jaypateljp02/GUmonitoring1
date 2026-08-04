from backend.database import SessionLocal
from backend.models.plug_telemetry import PlugTelemetry
from backend.models.room import Room
from backend.models.sensor import Sensor

db = SessionLocal()
plugs = db.query(PlugTelemetry.device_id).distinct().all()
print('DISTINCT PLUG DEVICE_IDs:', [p[0] for p in plugs])

for p in plugs:
    dev_id = p[0]
    count = db.query(PlugTelemetry).filter(PlugTelemetry.device_id == dev_id).count()
    latest = db.query(PlugTelemetry).filter(PlugTelemetry.device_id == dev_id).order_by(PlugTelemetry.timestamp.desc()).first()
    room = db.query(Room).join(Sensor).filter(Sensor.device_id == dev_id).first()
    r_name = room.name if room else 'NO ROOM MATCH'
    print(f'Device: {dev_id} | Room: {r_name} | Count: {count} | Latest Power: {latest.apower}W | Energy: {latest.today_energy}')

db.close()
