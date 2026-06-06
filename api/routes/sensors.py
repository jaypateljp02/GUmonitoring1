"""Sensor routes with auto-alert logic."""
from typing import List
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.database import get_db
from api.models.sensor import Sensor
from api.models.reading import SensorReading
from api.models.alert import Alert
from api.middleware.jwt_verify import get_current_user, require_admin, TokenUser
from api.schemas import ReadingCreate, ReadingResponse, SensorThresholdUpdate, MessageResponse, DeviceTelemetryResponse, MockControlRequest
from api.models.device_telemetry import DeviceTelemetry
from fastapi.responses import StreamingResponse
import csv
from io import StringIO
from api.worker import WorkerState

router = APIRouter(prefix="/sensors", tags=["Sensors"])


@router.post("/{sensor_id}/reading", response_model=ReadingResponse, status_code=201)
def submit_reading(
    sensor_id: str, req: ReadingCreate, db: Session = Depends(get_db), user: TokenUser = Depends(get_current_user)
):
    """
    Submit a sensor reading.
    Checks thresholds and auto-creates an Alert if out of bounds.
    """
    sensor = db.query(Sensor).filter(Sensor.id == sensor_id).first()
    if not sensor:
        raise HTTPException(status_code=404, detail="Sensor not found")

    reading = SensorReading(sensor_id=sensor.id, value=req.value)
    db.add(reading)

    # Threshold Check & Auto-Alert
    alert_msg = None
    if sensor.min_threshold is not None and req.value < sensor.min_threshold:
        alert_msg = f"{sensor.type.capitalize()} too low: {req.value} (Min: {sensor.min_threshold})"
    elif sensor.max_threshold is not None and req.value > sensor.max_threshold:
        alert_msg = f"{sensor.type.capitalize()} too high: {req.value} (Max: {sensor.max_threshold})"

    if alert_msg:
        # Prevent spamming alerts if one is already open for this sensor
        existing_alert = db.query(Alert).filter(Alert.sensor_id == sensor.id, Alert.resolved == False).first()
        if not existing_alert:
            db.add(Alert(sensor_id=sensor.id, value=req.value, message=alert_msg))

    db.commit()
    db.refresh(reading)
    return ReadingResponse.model_validate(reading)


@router.get("/{sensor_id}/history", response_model=List[ReadingResponse])
def get_history(
    sensor_id: str, days: int = 1, db: Session = Depends(get_db), user: TokenUser = Depends(get_current_user)
):
    cutoff = datetime.utcnow() - timedelta(days=days)
    readings = db.query(SensorReading).filter(
        SensorReading.sensor_id == sensor_id,
        SensorReading.recorded_at >= cutoff
    ).order_by(SensorReading.recorded_at.desc()).all()
    return [ReadingResponse.model_validate(r) for r in readings]


@router.put("/{sensor_id}/thresholds", response_model=MessageResponse)
def update_thresholds(
    sensor_id: str, req: SensorThresholdUpdate, db: Session = Depends(get_db), user: TokenUser = Depends(require_admin)
):
    sensor = db.query(Sensor).filter(Sensor.id == sensor_id).first()
    if not sensor:
        raise HTTPException(status_code=404, detail="Sensor not found")
    
    if req.min_threshold is not None:
        sensor.min_threshold = req.min_threshold
    if req.max_threshold is not None:
        sensor.max_threshold = req.max_threshold
        
    db.commit()
    return MessageResponse(message="Thresholds updated")

@router.get("/device/{device_id}/telemetry", response_model=List[DeviceTelemetryResponse])
def get_device_telemetry(
    device_id: str, days: int = 1, db: Session = Depends(get_db)
):
    cutoff = datetime.utcnow() - timedelta(days=days)
    logs = db.query(DeviceTelemetry).filter(
        DeviceTelemetry.device_id == device_id,
        DeviceTelemetry.timestamp >= cutoff
    ).order_by(DeviceTelemetry.timestamp.desc()).all()
    return [DeviceTelemetryResponse.model_validate(l) for l in logs]

@router.get("/device/{device_id}/export")
def export_device_telemetry(
    device_id: str, days: int = 1, db: Session = Depends(get_db)
):
    cutoff = datetime.utcnow() - timedelta(days=days)
    logs = db.query(DeviceTelemetry).filter(
        DeviceTelemetry.device_id == device_id,
        DeviceTelemetry.timestamp >= cutoff
    ).order_by(DeviceTelemetry.timestamp.desc()).all()
    
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Timestamp", "Temperature (C)", "Humidity (%)", "Battery (%)"])
    for log in logs:
        writer.writerow([str(log.id), log.timestamp.isoformat(), str(log.temperature), str(log.humidity), str(log.battery_level)])
        
    output.seek(0)
    response = StreamingResponse(iter([output.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename=telemetry_{device_id}.csv"
    return response

@router.post("/device/{device_id}/mock", response_model=MessageResponse)
def set_mock_state(
    device_id: str, req: MockControlRequest
):
    valid_states = ["normal", "ice", "warm", "failover"]
    if req.mode not in valid_states:
        raise HTTPException(status_code=400, detail=f"Invalid mode. Must be one of {valid_states}")
    WorkerState.MOCK_STATE = req.mode
    return MessageResponse(message=f"Mock state set to {req.mode}")

@router.get("/device/{device_id}/sensors")
def list_device_sensors(device_id: str, db: Session = Depends(get_db)):
    """Public endpoint: list all sensors for a device with their thresholds."""
    sensors = db.query(Sensor).filter(Sensor.device_id == device_id, Sensor.active == True).all()
    return [
        {
            "id": str(s.id),
            "name": s.name,
            "type": s.type,
            "min_threshold": float(s.min_threshold) if s.min_threshold is not None else None,
            "max_threshold": float(s.max_threshold) if s.max_threshold is not None else None,
        }
        for s in sensors
    ]

@router.put("/device/{device_id}/thresholds")
def update_device_thresholds(device_id: str, req: dict, db: Session = Depends(get_db)):
    """Public endpoint: update thresholds for a device's sensors."""
    sensors = db.query(Sensor).filter(Sensor.device_id == device_id, Sensor.active == True).all()
    if not sensors:
        raise HTTPException(status_code=404, detail="Device not found")
    for s in sensors:
        if s.type == "temperature":
            if "temp_min" in req:
                s.min_threshold = req["temp_min"]
            if "temp_max" in req:
                s.max_threshold = req["temp_max"]
        elif s.type == "humidity":
            if "hum_min" in req:
                s.min_threshold = req["hum_min"]
            if "hum_max" in req:
                s.max_threshold = req["hum_max"]
    db.commit()
    return {"message": "Thresholds updated"}

