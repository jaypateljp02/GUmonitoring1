
import sys
sys.path.append('C:/GroundUp/ground-up-monitoring')
from backend.database import SessionLocal
from backend.models.room import Room
from backend.models.sensor import Sensor

db = SessionLocal()

plug_freezer = db.query(Sensor).filter(Sensor.type == \'plug\', Sensor.name.ilike(\'Freezer%\')).first()
room_freezer = db.query(Room).filter(Room.name == \'Freezer \').first()

if plug_freezer and room_freezer:
    plug_freezer.room_id = room_freezer.id
    db.commit()
    print(f\'Fixed {plug_freezer.name} to {room_freezer.name}\')

plug_hall_freezer = db.query(Sensor).filter(Sensor.type == \'plug\', Sensor.name.ilike(\'Hall freezer and fridge%\')).first()
room_hall_freezer = db.query(Room).filter(Room.name == \'Hall freezer and fridge (6)\').first()

if plug_hall_freezer and room_hall_freezer:
    plug_hall_freezer.room_id = room_hall_freezer.id
    db.commit()
    print(f\'Fixed {plug_hall_freezer.name} to {room_hall_freezer.name}\')

