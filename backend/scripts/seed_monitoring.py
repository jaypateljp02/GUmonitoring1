import os
import sys
import uuid

# Add the project root to the path so we can import from backend
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from backend.database import SessionLocal, engine, Base
from backend.models.room import Room
from backend.models.sensor import Sensor

def seed():
    # Base.metadata.create_all(bind=engine) # Assuming migrations or manual creation exists
    db = SessionLocal()
    
    # 1. Clear existing monitoring data (if this is safe to do during dev)
    print("Clearing old data...")
    db.query(Sensor).delete()
    db.query(Room).delete()
    db.commit()

    locations = [
        {"name": "Room 1", "type": "room", "map_x": "25%", "map_y": "40%"},
        {"name": "Room 3", "type": "room", "map_x": "75%", "map_y": "40%"},
        {"name": "Main Kitchen", "type": "room", "map_x": "50%", "map_y": "70%"},
        {"name": "Rooftop", "type": "room", "map_x": "50%", "map_y": "10%"},
        
        {"name": "Fridge 1", "type": "fridge", "map_x": "85%", "map_y": "60%"},
        {"name": "Fridge 2", "type": "fridge", "map_x": "85%", "map_y": "65%"},
        {"name": "Fridge 3", "type": "fridge", "map_x": "85%", "map_y": "70%"},
        {"name": "Fridge 4", "type": "fridge", "map_x": "85%", "map_y": "75%"},
        {"name": "Fridge 5", "type": "fridge", "map_x": "85%", "map_y": "80%"},
        {"name": "Fridge 6", "type": "fridge", "map_x": "85%", "map_y": "85%"},
        
        {"name": "Freezer 1", "type": "freezer", "map_x": "15%", "map_y": "60%"},
        {"name": "Freezer 2", "type": "freezer", "map_x": "15%", "map_y": "70%"},
        {"name": "Freezer 3", "type": "freezer", "map_x": "15%", "map_y": "80%"},
    ]

    print("Inserting locations...")
    rooms_created = []
    for loc in locations:
        room = Room(
            id=uuid.uuid4(),
            name=loc["name"],
            type=loc["type"],
            map_x=loc["map_x"],
            map_y=loc["map_y"]
        )
        db.add(room)
        rooms_created.append(room)
    
    db.commit()

    print("Inserting sensors...")
    for room in rooms_created:
        # Every room/fridge gets a temperature sensor
        temp_sensor = Sensor(
            id=uuid.uuid4(),
            room_id=room.id,
            name=f"{room.name} Temp",
            type="temperature",
            device_id=str(uuid.uuid4())[:10], # Mock device ID
            min_threshold=2.0 if room.type in ["fridge", "freezer"] else 18.0,
            max_threshold=6.0 if room.type == "fridge" else (-5.0 if room.type == "freezer" else 28.0)
        )
        db.add(temp_sensor)
        
        # Only rooms get humidity sensors
        if room.type == "room":
            hum_sensor = Sensor(
                id=uuid.uuid4(),
                room_id=room.id,
                name=f"{room.name} Hum",
                type="humidity",
                device_id=temp_sensor.device_id, # Same device
                min_threshold=40.0,
                max_threshold=65.0
            )
            db.add(hum_sensor)

    db.commit()
    print("Seed complete!")
    db.close()

if __name__ == "__main__":
    seed()
