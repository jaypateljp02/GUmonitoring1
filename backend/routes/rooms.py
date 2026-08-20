"""Room routes."""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from backend.database import get_db
from backend.models.room import Room
from backend.models.sensor import Sensor
from backend.middleware.jwt_verify import get_current_user, require_admin, TokenUser
from backend.schemas import RoomCreate, RoomResponse, SensorResponse, MessageResponse

router = APIRouter(prefix="/rooms", tags=["Rooms"])


@router.get("", response_model=List[RoomResponse])
def list_rooms(db: Session = Depends(get_db)):
    rooms = db.query(Room).filter(Room.active == True).all()
    
    # Fetch ALL active sensors in one query instead of N queries per room
    all_sensors = db.query(Sensor).filter(Sensor.active == True).all()
    # Group by room_id
    sensors_by_room = {}
    for s in all_sensors:
        if s.room_id:
            sensors_by_room.setdefault(str(s.room_id), []).append(s)
    
    result = []
    for r in rooms:
        resp = RoomResponse.model_validate(r)
        room_sensors = sensors_by_room.get(str(r.id), [])
        resp.sensors = [SensorResponse.model_validate(s) for s in room_sensors]
        result.append(resp)
    return result


@router.get("/{room_id}", response_model=RoomResponse)
def get_room(room_id: str, db: Session = Depends(get_db)):
    room = db.query(Room).filter(Room.id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    resp = RoomResponse.model_validate(room)
    sensors = db.query(Sensor).filter(Sensor.room_id == room.id, Sensor.active == True).all()
    resp.sensors = [SensorResponse.model_validate(s) for s in sensors]
    return resp


@router.post("", response_model=RoomResponse, status_code=201)
def create_room(req: RoomCreate, db: Session = Depends(get_db), user: TokenUser = Depends(require_admin)):
    room = Room(**req.model_dump())
    db.add(room)
    db.commit()
    db.refresh(room)
    return RoomResponse.model_validate(room)


class RoomCoordinatesUpdate(BaseModel):
    map_x: Optional[str] = None
    map_y: Optional[str] = None


@router.put("/{room_id}/coordinates", response_model=MessageResponse)
def update_room_coordinates(room_id: str, req: RoomCoordinatesUpdate, db: Session = Depends(get_db)):
    room = db.query(Room).filter(Room.id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    room.map_x = req.map_x
    room.map_y = req.map_y
    db.commit()
    return MessageResponse(message="Room coordinates updated successfully")


@router.post("/{room_id}/maintenance/{action}")
def toggle_room_maintenance(room_id: str, action: str, db: Session = Depends(get_db)):
    """
    Toggle cleaning / maintenance mode for a room or appliance (start or stop).
    Pauses alert notifications during cleaning mode.
    """
    if action not in ["start", "stop"]:
        raise HTTPException(status_code=400, detail="Action must be 'start' or 'stop'")
        
    room = db.query(Room).filter(Room.id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    is_under_maint = (action == "start")
    
    try:
        room.is_under_maintenance = is_under_maint
        db.commit()
    except Exception as e:
        db.rollback()
        # Fallback if DB column is missing, execute ALTER TABLE dynamically
        try:
            db.execute("ALTER TABLE monitoring.rooms ADD COLUMN IF NOT EXISTS is_under_maintenance BOOLEAN DEFAULT FALSE;")
            db.commit()
            room.is_under_maintenance = is_under_maint
            db.commit()
        except Exception:
            pass

    return {
        "status": "ok",
        "action": action,
        "room_id": str(room_id),
        "is_under_maintenance": is_under_maint,
        "message": f"Cleaning mode {'activated' if is_under_maint else 'deactivated'} for {room.name}"
    }
