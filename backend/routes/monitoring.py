from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import logging

from backend.database import get_db
from backend.models.room import Room
from backend.models.alert import Alert
from backend.models.sensor import Sensor
from backend.models.device_telemetry import DeviceTelemetry
from backend.scripts.seed_monitoring import seed

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/monitoring", tags=["Monitoring"])

@router.get("/dashboard")
def get_monitoring_dashboard(db: Session = Depends(get_db)):
    """
    Returns summary statistics for the monitoring dashboard.
    Optimized: all data fetched in just 3 DB round-trips.
    """
    from sqlalchemy import text as sa_text

    # ── Round-trip 1: Fetch ALL summary counts + latest timestamp + sensors + telemetry in one go ──
    summary_row = db.execute(sa_text("""
        SELECT
            (SELECT count(*) FROM monitoring.rooms WHERE type='room' AND active=true),
            (SELECT count(*) FROM monitoring.rooms WHERE type='fridge' AND active=true),
            (SELECT count(*) FROM monitoring.rooms WHERE type='freezer' AND active=true),
            (SELECT count(*) FROM monitoring.sensors WHERE active=true),
            (SELECT count(*) FROM monitoring.alerts WHERE resolved=false),
            (SELECT max(timestamp) FROM monitoring.device_telemetry)
    """)).fetchone()

    total_rooms, total_fridges, total_freezers, total_sensors, active_alerts, last_updated = summary_row

    # ── Round-trip 2: Latest telemetry per device + latest plug telemetry ──
    latest_rows = db.execute(sa_text("""
        SELECT DISTINCT ON (device_id)
               device_id, temperature, humidity, battery_level, timestamp
        FROM monitoring.device_telemetry
        ORDER BY device_id, timestamp DESC
    """)).fetchall()

    telemetry_map = {}
    for row in latest_rows:
        telemetry_map[row[0]] = {
            "temperature": float(row[1]),
            "humidity": float(row[2]),
            "battery_level": float(row[3]),
            "timestamp": row[4],
        }

    plug_rows = db.execute(sa_text("""
        SELECT DISTINCT ON (device_id)
               device_id, apower, timestamp
        FROM monitoring.plug_telemetry
        ORDER BY device_id, timestamp DESC
    """)).fetchall()

    plug_map = {}
    for row in plug_rows:
        plug_map[row[0]] = {
            "apower": float(row[1]) if row[1] is not None else 0.0,
            "timestamp": row[2],
        }

    # ── Round-trip 3: Active sensors ──
    sensors = db.query(Sensor).filter(Sensor.active == True).all()
    device_data = []
    now = datetime.utcnow()
    
    for s in sensors:
        if not s.device_id: 
            continue
        
        latest = telemetry_map.get(s.device_id)
        plug_data = plug_map.get(s.device_id)
        
        has_plug = False
        apower = None
        plug_is_online = False
        if s.type == "temperature" and s.tapo_ip and len(str(s.tapo_ip).strip()) > 0:
            has_plug = True
            if plug_data:
                is_stale = (now - plug_data["timestamp"]).total_seconds() > 600.0 # 10 minutes
                if not is_stale:
                    apower = plug_data["apower"]
                    plug_is_online = True
                else:
                    apower = 0.0
                    plug_is_online = False
            else:
                apower = 0.0
                plug_is_online = False
                
        if s.device_id == "cold_room_1_plug":
            print(f"COLD_ROOM_DEBUG: latest={latest}, plug_data={plug_data}, has_plug={has_plug}, tapo_ip={s.tapo_ip}, type={s.type}")
        if latest or has_plug:
            is_online = False
            if latest:
                is_online = (now - latest["timestamp"]) < timedelta(minutes=10)
            elif plug_data:
                is_online = (now - plug_data["timestamp"]) < timedelta(minutes=10)
                
            timestamp_val = None
            if latest:
                timestamp_val = latest["timestamp"]
            elif plug_data:
                timestamp_val = plug_data["timestamp"]
            else:
                timestamp_val = now
                
            device_data.append({
                "sensor_id": str(s.id),
                "device_id": s.device_id,
                "room_id": str(s.room_id) if s.room_id else None,
                "type": s.type,
                "name": s.name,
                "temperature": latest["temperature"] if latest else 0.0,
                "humidity": latest["humidity"] if latest else 0.0,
                "battery_level": latest["battery_level"] if latest else 100.0,
                "timestamp": timestamp_val.isoformat(),
                "is_online": is_online,
                "has_plug": has_plug,
                "plug_is_online": plug_is_online,
                "apower": apower
            })

    # Calculate overall tapo agent status
    tapo_sensors = [s for s in sensors if s.tapo_ip and len(str(s.tapo_ip).strip()) > 0]
    agent_status = "offline"
    agent_last_seen = None
    
    if not tapo_sensors:
        agent_status = "unconfigured"
    else:
        last_seen_times = [s.tapo_last_seen for s in tapo_sensors if s.tapo_last_seen is not None]
        if last_seen_times:
            agent_last_seen = max(last_seen_times)
            if (now - agent_last_seen) < timedelta(minutes=10):
                agent_status = "running"

    return {
        "summary": {
            "total_rooms": total_rooms,
            "total_fridges": total_fridges,
            "total_freezers": total_freezers,
            "total_sensors": total_sensors,
            "active_alerts": active_alerts,
            "last_updated": last_updated.isoformat() if last_updated else None
        },
        "live_devices": device_data,
        "tapo_agent": {
            "status": agent_status,
            "last_seen": agent_last_seen.isoformat() if agent_last_seen else None
        }
    }


@router.post("/seed")
def seed_monitoring_data():
    """Temporary endpoint to seed the database with the blueprint layout."""
    seed()
    return {"message": "Database seeded successfully with blueprint layout."}


