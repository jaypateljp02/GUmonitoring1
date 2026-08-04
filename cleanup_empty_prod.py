import sys
sys.path.append('C:/GroundUp/ground-up-monitoring')
from backend.database import SessionLocal
from backend.models.room import Room
from backend.models.sensor import Sensor

db = SessionLocal()
rooms = db.query(Room).all()
for r in rooms:
    sensors = db.query(Sensor).filter(Sensor.room_id == r.id).all()
    if not sensors:
        print(f'Deleting empty room: {r.name}')
        db.delete(r)

db.commit()
print('Cleanup complete!')
