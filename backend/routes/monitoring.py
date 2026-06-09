from typing import List, Dict, Any
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.database import get_db
from backend.models.room import Room
from backend.models.alert import Alert
from backend.models.sensor import Sensor
from backend.models.device_telemetry import DeviceTelemetry
from backend.middleware.jwt_verify import get_current_user, TokenUser
from backend.scripts.seed_monitoring import seed

router = APIRouter(prefix="/monitoring", tags=["Monitoring"])

@router.get("/dashboard")
def get_monitoring_dashboard(db: Session = Depends(get_db)):
    """
    Returns summary statistics for the monitoring dashboard:
    - all rooms status
    - fridge count
    - freezer count
    - active alerts
    - last updated time
    """
    total_rooms = db.query(Room).filter(Room.type == 'room').count()
    total_fridges = db.query(Room).filter(Room.type == 'fridge').count()
    total_freezers = db.query(Room).filter(Room.type == 'freezer').count()
    total_sensors = db.query(Sensor).count()
    
    active_alerts = db.query(Alert).filter(Alert.resolved == False).count()
    
    # Get the latest telemetry record to find the "last updated time"
    latest_log = db.query(DeviceTelemetry).order_by(DeviceTelemetry.timestamp.desc()).first()
    last_updated = latest_log.timestamp if latest_log else None

    # Get a quick status of all devices for the map view
    # For a small facility, we can just fetch all sensors and their latest telemetry
    # (In a large scale app, this would be cached)
    sensors = db.query(Sensor).filter(Sensor.active == True).all()
    device_data = []
    
    for s in sensors:
        if not s.device_id: continue
        # Find latest reading for this specific device
        latest = db.query(DeviceTelemetry).filter(DeviceTelemetry.device_id == s.device_id).order_by(DeviceTelemetry.timestamp.desc()).first()
        if latest:
            device_data.append({
                "sensor_id": str(s.id),
                "room_id": str(s.room_id) if s.room_id else None,
                "type": s.type,
                "name": s.name,
                "temperature": float(latest.temperature),
                "humidity": float(latest.humidity),
                "battery_level": float(latest.battery_level),
                "timestamp": latest.timestamp.isoformat()
            })

    return {
        "summary": {
            "total_rooms": total_rooms,
            "total_fridges": total_fridges,
            "total_freezers": total_freezers,
            "total_sensors": total_sensors,
            "active_alerts": active_alerts,
            "last_updated": last_updated.isoformat() if last_updated else None
        },
        "live_devices": device_data
    }

@router.post("/seed")
def seed_monitoring_data():
    """Temporary endpoint to seed the database with the blueprint layout."""
    seed()
    return {"message": "Database seeded successfully with blueprint layout."}
