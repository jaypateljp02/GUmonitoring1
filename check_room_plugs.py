from backend.database import SessionLocal
from backend.models.plug_telemetry import PlugTelemetry
from backend.models.room import Room
from backend.models.sensor import Sensor

db = SessionLocal()
rooms = db.query(Room).all()

for r in rooms:
    sensors = db.query(Sensor).filter(Sensor.room_id == r.id).all()
    dev_ids = [s.device_id for s in sensors if s.device_id]
    plug_count = db.query(PlugTelemetry).filter(PlugTelemetry.device_id.in_(dev_ids)).count() if dev_ids else 0
    latest_plug = db.query(PlugTelemetry).filter(PlugTelemetry.device_id.in_(dev_ids)).order_by(PlugTelemetry.timestamp.desc()).first() if dev_ids else None
    power_str = str(latest_plug.apower) if latest_plug else 'N/A'
    print('Room:', r.name, '| Sensors:', len(sensors), '| Plug Logs:', plug_count, '| Latest Power:', power_str, 'W')

db.close()
