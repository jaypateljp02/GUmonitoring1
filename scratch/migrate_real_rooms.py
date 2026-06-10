import sys
import os
import uuid
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.models.room import Room
from backend.models.sensor import Sensor

# Coordinates mapping for the new floor plan
real_rooms_config = [
    {"name": "Vinegar room", "type": "room", "map_x": "56%", "map_y": "20%"},
    {"name": "Miso room", "type": "room", "map_x": "80%", "map_y": "64%"},
    {"name": "Wild room (4)", "type": "room", "map_x": "68%", "map_y": "64%"},
    {"name": "Fridge", "type": "fridge", "map_x": "45%", "map_y": "52%"},
    {"name": "Fridge (5)", "type": "fridge", "map_x": "89%", "map_y": "33%"},
    {"name": "Living room freezer 6", "type": "freezer", "map_x": "81%", "map_y": "23%"},
    {"name": "Terrace fridge (7)", "type": "fridge", "map_x": "63%", "map_y": "23%"},
    {"name": "8", "type": "freezer", "map_x": "78%", "map_y": "39%"}
]

# Real eWeLink Device IDs
device_ids = {
    "Vinegar room": "a4b0028991",
    "Miso room": "a4b002898f",
    "Wild room (4)": "a4b0028a85",
    "Fridge": "a4b002884e",
    "Fridge (5)": "a4b0028a86",
    "Living room freezer 6": "a4b0028a87",
    "Terrace fridge (7)": "a4b0028a88",
    "8": "a4b0028aa7"
}

def migrate_database(db_url, db_name):
    print(f"\n--- Migrating {db_name} ---")
    try:
        engine = create_engine(db_url)
        Session = sessionmaker(bind=engine)
        db = Session()
        
        # 1. Deactivate old mock rooms
        mock_room_names = ["Room 1", "Room 3", "Main Kitchen", "Rooftop", "Fridge 1", "Fridge 2", "Fridge 3", "Fridge 4", "Fridge 5", "Fridge 6", "Freezer 1", "Freezer 2", "Freezer 3"]
        db.query(Room).filter(Room.name.in_(mock_room_names)).update({"active": False}, synchronize_session=False)
        db.query(Sensor).filter(Sensor.name.like("%Temp"), Sensor.device_id.notin_(device_ids.values())).update({"active": False}, synchronize_session=False)
        db.query(Sensor).filter(Sensor.name.like("%Hum"), Sensor.device_id.notin_(device_ids.values())).update({"active": False}, synchronize_session=False)
        
        # 2. Create or Update Real Rooms & Coordinates
        for cfg in real_rooms_config:
            room_name = cfg["name"]
            device_id = device_ids[room_name]
            
            # Check if room already exists
            room = db.query(Room).filter(Room.name == room_name).first()
            if not room:
                room = Room(
                    id=uuid.uuid4(),
                    name=room_name,
                    type=cfg["type"],
                    map_x=cfg["map_x"],
                    map_y=cfg["map_y"],
                    active=True
                )
                db.add(room)
                db.flush()
                print(f"Created room: {room_name}")
            else:
                room.active = True
                room.type = cfg["type"]
                room.map_x = cfg["map_x"]
                room.map_y = cfg["map_y"]
                print(f"Updated room: {room_name} (Coordinates: {cfg['map_x']}, {cfg['map_y']})")
                
            # 3. Find and link existing sensors for this device ID to the room
            linked_count = db.query(Sensor).filter(Sensor.device_id == device_id).update({"room_id": room.id, "active": True}, synchronize_session=False)
            print(f"  Linked {linked_count} sensors for device {device_id} to room {room_name}")
            
            # If no sensors exist, we let worker.py create them on sync, but let's make sure they exist
            # in case worker hasn't run yet.
            temp_sensor = db.query(Sensor).filter(Sensor.room_id == room.id, Sensor.type == "temperature").first()
            if not temp_sensor:
                temp_sensor = Sensor(
                    id=uuid.uuid4(),
                    room_id=room.id,
                    name=f"{room_name} Temp",
                    type="temperature",
                    device_id=device_id,
                    min_threshold=2.0 if room.type in ["fridge", "freezer"] else 18.0,
                    max_threshold=6.0 if room.type == "fridge" else (-5.0 if room.type == "freezer" else 28.0),
                    active=True
                )
                db.add(temp_sensor)
                print(f"  Created temperature sensor for {room_name}")
                
            if room.type == "room":
                hum_sensor = db.query(Sensor).filter(Sensor.room_id == room.id, Sensor.type == "humidity").first()
                if not hum_sensor:
                    hum_sensor = Sensor(
                        id=uuid.uuid4(),
                        room_id=room.id,
                        name=f"{room_name} Hum",
                        type="humidity",
                        device_id=device_id,
                        min_threshold=40.0,
                        max_threshold=65.0,
                        active=True
                    )
                    db.add(hum_sensor)
                    print(f"  Created humidity sensor for {room_name}")
                    
        db.commit()
        print(f"Migration successful for {db_name}!")
    except Exception as e:
        print(f"Error migrating {db_name}: {e}")
        if 'db' in locals():
            db.rollback()
    finally:
        if 'db' in locals():
            db.close()

if __name__ == "__main__":
    # Local Database Migration
    local_url = "postgresql://postgres:1234@localhost:5432/groundup"
    migrate_database(local_url, "Local Database")
    
    # Live Render Database Migration
    live_url = "postgresql://groundup_db_user:8l3J4zaWLyq1rqc2dQAiVWjdhdD9rNhZ@dpg-d8i09h4m0tmc73c646vg-a.singapore-postgres.render.com/groundup_db"
    migrate_database(live_url, "Render Database")
