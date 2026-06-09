from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
import logging
import uuid

from backend.database import get_db
from backend.models.room import Room
from backend.models.alert import Alert
from backend.models.sensor import Sensor
from backend.models.device_telemetry import DeviceTelemetry
from backend.models.ewelink_token import EwelinkToken
from backend.services.ewelink import EwelinkClient
from backend.scripts.seed_monitoring import seed

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/monitoring", tags=["Monitoring"])

class OauthCallbackBody(BaseModel):
    code: str
    redirect_uri: str = "https://google.com/callback"

async def sync_ewelink_devices_to_db(db: Session, access_token: str):
    """
    Query all devices from eWeLink and sync them to Rooms and Sensors in the database.
    Deactivates any existing sensors (like mock ones) that are not present in eWeLink.
    """
    client = EwelinkClient(access_token=access_token)
    thing_list = await client.get_all_devices()
    if thing_list is None:
        logger.error("Failed to retrieve eWeLink devices during sync.")
        return False

    synced_device_ids = []
    
    for t in thing_list:
        item_data = t.get("itemData", {})
        device_id = item_data.get("deviceid")
        if not device_id:
            continue
        
        device_name = item_data.get("name") or f"Device {device_id[:6]}"
        synced_device_ids.append(device_id)

        # 1. Find or create Room
        existing_sensor = db.query(Sensor).filter(Sensor.device_id == device_id).first()
        if existing_sensor:
            room_id = existing_sensor.room_id
            room = db.query(Room).filter(Room.id == room_id).first()
            if room and room.name != device_name:
                room.name = device_name
        else:
            # Determine room type
            room_type = "room"
            name_lower = device_name.lower()
            if "fridge" in name_lower:
                room_type = "fridge"
            elif "freezer" in name_lower:
                room_type = "freezer"
                
            room = Room(
                name=device_name,
                type=room_type,
                active=True
            )
            db.add(room)
            db.flush() # Populate room.id
            room_id = room.id

        # 2. Find or create Temperature Sensor
        temp_sensor = db.query(Sensor).filter(Sensor.room_id == room_id, Sensor.type == "temperature").first()
        if not temp_sensor:
            temp_sensor = Sensor(
                room_id=room_id,
                name=f"{device_name} Temp",
                type="temperature",
                device_id=device_id,
                min_threshold=2.0 if room.type in ["fridge", "freezer"] else 18.0,
                max_threshold=6.0 if room.type == "fridge" else (-5.0 if room.type == "freezer" else 28.0),
                active=True
            )
            db.add(temp_sensor)
        else:
            temp_sensor.active = True

        # 3. Find or create Humidity Sensor
        hum_sensor = db.query(Sensor).filter(Sensor.room_id == room_id, Sensor.type == "humidity").first()
        if not hum_sensor:
            hum_sensor = Sensor(
                room_id=room_id,
                name=f"{device_name} Hum",
                type="humidity",
                device_id=device_id,
                min_threshold=40.0,
                max_threshold=65.0,
                active=True
            )
            db.add(hum_sensor)
        else:
            hum_sensor.active = True

    # 4. Deactivate mock/removed sensors and clean up inactive rooms
    if synced_device_ids:
        # Deactivate sensors not in eWeLink list
        db.query(Sensor).filter(Sensor.device_id.notin_(synced_device_ids)).update({"active": False}, synchronize_session=False)
        
        # Deactivate rooms that have no active sensors
        active_room_ids = db.query(Sensor.room_id).filter(Sensor.active == True).distinct().all()
        active_room_ids = [r[0] for r in active_room_ids if r[0]]
        
        db.query(Room).filter(Room.id.notin_(active_room_ids)).update({"active": False}, synchronize_session=False)
        
    db.commit()
    logger.info(f"Sync complete. Active device IDs: {synced_device_ids}")
    return True


@router.get("/dashboard")
def get_monitoring_dashboard(db: Session = Depends(get_db)):
    """
    Returns summary statistics for the monitoring dashboard
    """
    total_rooms = db.query(Room).filter(Room.type == 'room', Room.active == True).count()
    total_fridges = db.query(Room).filter(Room.type == 'fridge', Room.active == True).count()
    total_freezers = db.query(Room).filter(Room.type == 'freezer', Room.active == True).count()
    total_sensors = db.query(Sensor).filter(Sensor.active == True).count()
    
    active_alerts = db.query(Alert).filter(Alert.resolved == False).count()
    
    # Get the latest telemetry log
    latest_log = db.query(DeviceTelemetry).order_by(DeviceTelemetry.timestamp.desc()).first()
    last_updated = latest_log.timestamp if latest_log else None

    # Get live devices status
    sensors = db.query(Sensor).filter(Sensor.active == True).all()
    device_data = []
    
    for s in sensors:
        if not s.device_id: 
            continue
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


@router.get("/oauth/url")
def get_oauth_url():
    """
    Generate the eWeLink authorization URL
    """
    client = EwelinkClient()
    # Construct index authorization URL
    auth_url = (
        f"https://c2ccdn.coolkit.cc/oauth/index.html"
        f"?clientId={client.appid}"
        f"&redirectUrl=https%3A%2F%2Fgoogle.com%2Fcallback"
        f"&grantType=authorization_code"
        f"&nonce=12345678"
    )
    return {"url": auth_url}


@router.post("/oauth/callback")
async def oauth_callback(body: OauthCallbackBody, db: Session = Depends(get_db)):
    """
    Exchange authorization code for access/refresh token and store them in the database
    """
    client = EwelinkClient()
    token_data = await client.exchange_code(body.code, body.redirect_uri)
    if not token_data:
        raise HTTPException(status_code=400, detail="Failed to exchange authorization code with eWeLink.")
    
    access_token = token_data.get("at")
    refresh_token = token_data.get("rt")
    region = token_data.get("region", "as")
    
    if not access_token or not refresh_token:
        raise HTTPException(status_code=400, detail="Authorization response is missing tokens.")
    
    # Save to DB
    token_record = db.query(EwelinkToken).filter(EwelinkToken.id == "default").first()
    if token_record:
        token_record.access_token = access_token
        token_record.refresh_token = refresh_token
        token_record.region = region
    else:
        token_record = EwelinkToken(
            id="default",
            access_token=access_token,
            refresh_token=refresh_token,
            region=region
        )
        db.add(token_record)
    
    db.commit()
    
    # Trigger an immediate sync of eWeLink devices
    sync_success = await sync_ewelink_devices_to_db(db, access_token)
    
    return {
        "message": "eWeLink account linked successfully!",
        "synced": sync_success
    }


@router.get("/oauth/status")
def get_oauth_status(db: Session = Depends(get_db)):
    """
    Check if the eWeLink account is currently linked
    """
    token_record = db.query(EwelinkToken).filter(EwelinkToken.id == "default").first()
    return {"linked": token_record is not None}


@router.post("/oauth/sync")
async def trigger_manual_sync(db: Session = Depends(get_db)):
    """
    Manually synchronize eWeLink devices
    """
    token_record = db.query(EwelinkToken).filter(EwelinkToken.id == "default").first()
    if not token_record:
        raise HTTPException(status_code=400, detail="eWeLink account is not linked.")
    
    sync_success = await sync_ewelink_devices_to_db(db, token_record.access_token)
    if not sync_success:
        raise HTTPException(status_code=500, detail="Failed to sync devices.")
        
    return {"message": "Devices synced successfully!"}


@router.post("/seed")
def seed_monitoring_data():
    """Temporary endpoint to seed the database with the blueprint layout."""
    seed()
    return {"message": "Database seeded successfully with blueprint layout."}
