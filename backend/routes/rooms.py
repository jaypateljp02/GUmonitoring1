"""Room routes."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.room import Room
from backend.models.sensor import Sensor
from backend.middleware.jwt_verify import get_current_user, require_admin, TokenUser
from backend.schemas import RoomCreate, RoomResponse, SensorResponse, MessageResponse

router = APIRouter(prefix="/rooms", tags=["Rooms"])


@router.get("", response_model=List[RoomResponse])
def list_rooms(db: Session = Depends(get_db), user: TokenUser = Depends(get_current_user)):
    rooms = db.query(Room).filter(Room.active == True).all()
    result = []
    for r in rooms:
        resp = RoomResponse.model_validate(r)
        sensors = db.query(Sensor).filter(Sensor.room_id == r.id, Sensor.active == True).all()
        resp.sensors = [SensorResponse.model_validate(s) for s in sensors]
        result.append(resp)
    return result


@router.get("/{room_id}", response_model=RoomResponse)
def get_room(room_id: str, db: Session = Depends(get_db), user: TokenUser = Depends(get_current_user)):
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
