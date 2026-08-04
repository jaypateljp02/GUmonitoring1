import sys
sys.path.append('C:/GroundUp/ground-up-monitoring')
from backend.database import SessionLocal
from backend.models.room import Room
from backend.models.sensor import Sensor

db = SessionLocal()

def get_room(name_like):
    return db.query(Room).filter(Room.name.ilike(name_like)).first()

def move_plug(plug_name_like, target_room_name_like):
    plug = db.query(Sensor).filter(Sensor.type == 'plug', Sensor.name.ilike(plug_name_like)).first()
    target_room = get_room(target_room_name_like)
    if plug and target_room:
        old_room = plug.room_id
        plug.room_id = target_room.id
        print(f'Moved {plug.name} from {old_room} to {target_room.name} ({target_room.id})')
        db.commit()
    else:
        print(f'Could not find plug ({plug_name_like}) or room ({target_room_name_like})')

move_plug('%Terrace fridge steel%', '%Terrace fridge steel (7)%')
move_plug('%Terrace fridge%', '%Terrace fridge (9)%')
move_plug('%Hall freezer and fridge%', '%Hall freezer and fridge (6)%')
move_plug('%Samsung fridge and freezer%', '%Samsung tall fridge and freezer (11)%')
move_plug('%White Fridge%', '%White fridge%')
move_plug('%Hall white fridge%', '%Hall white fridge (13)%')
move_plug('%Freezer%', '%Freezer%')
move_plug('%Black Fridge%', '%Black Fridge%')
