"""Sensor routes with auto-alert logic."""
from typing import List
from datetime import datetime, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.sensor import Sensor
from backend.models.reading import SensorReading
from backend.models.alert import Alert
from backend.middleware.jwt_verify import get_current_user, require_admin, TokenUser
from backend.schemas import ReadingCreate, ReadingResponse, SensorThresholdUpdate, MessageResponse, DeviceTelemetryResponse, MockControlRequest
from backend.models.device_telemetry import DeviceTelemetry
from fastapi.responses import StreamingResponse
import csv
from io import StringIO
from backend.worker import WorkerState

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

def aggregate_telemetry(logs: List[DeviceTelemetry], interval_minutes: int = 30) -> List[DeviceTelemetryResponse]:
    if not logs:
        return []
    
    # Sort logs ascending by timestamp for proper grouping
    sorted_logs = sorted(logs, key=lambda x: x.timestamp)
    
    # If interval is raw/no-aggregation, return directly
    if interval_minutes <= 1:
        return [
            DeviceTelemetryResponse(
                id=log.id,
                device_id=log.device_id,
                timestamp=log.timestamp.replace(second=0, microsecond=0),
                temperature=Decimal(str(round(log.temperature, 2))),
                humidity=Decimal(str(round(log.humidity, 2))),
                battery_level=Decimal(str(round(log.battery_level, 2)))
            )
            for log in reversed(sorted_logs)
        ]
        
    aggregated = []
    current_bucket_start = None
    bucket_temps = []
    bucket_hums = []
    bucket_bats = []
    
    epoch = datetime(1970, 1, 1)
    
    for log in sorted_logs:
        # Round timestamp dynamically to any interval_minutes using epoch
        delta = log.timestamp - epoch
        total_minutes = int(delta.total_seconds() // 60)
        rounded_minutes = (total_minutes // interval_minutes) * interval_minutes
        bucket_time = epoch + timedelta(minutes=rounded_minutes)
        
        if current_bucket_start is None:
            current_bucket_start = bucket_time
            
        if bucket_time == current_bucket_start:
            bucket_temps.append(log.temperature)
            bucket_hums.append(log.humidity)
            bucket_bats.append(log.battery_level)
        else:
            # Save the average for the finished bucket
            avg_temp = sum(bucket_temps) / len(bucket_temps)
            avg_hum = sum(bucket_hums) / len(bucket_hums)
            avg_bat = sum(bucket_bats) / len(bucket_bats)
            
            aggregated.append(DeviceTelemetryResponse(
                id=sorted_logs[0].id,
                device_id=sorted_logs[0].device_id,
                timestamp=current_bucket_start,
                temperature=Decimal(str(round(avg_temp, 2))),
                humidity=Decimal(str(round(avg_hum, 2))),
                battery_level=Decimal(str(round(avg_bat, 2)))
            ))
            
            # Start new bucket
            current_bucket_start = bucket_time
            bucket_temps = [log.temperature]
            bucket_hums = [log.humidity]
            bucket_bats = [log.battery_level]
            
    # Don't forget the last bucket
    if current_bucket_start is not None:
        avg_temp = sum(bucket_temps) / len(bucket_temps)
        avg_hum = sum(bucket_hums) / len(bucket_hums)
        avg_bat = sum(bucket_bats) / len(bucket_bats)
        aggregated.append(DeviceTelemetryResponse(
            id=sorted_logs[0].id,
            device_id=sorted_logs[0].device_id,
            timestamp=current_bucket_start,
            temperature=Decimal(str(round(avg_temp, 2))),
            humidity=Decimal(str(round(avg_hum, 2))),
            battery_level=Decimal(str(round(avg_bat, 2)))
        ))
        
    # Return sorted descending (newest first)
    return list(reversed(aggregated))

@router.get("/device/{device_id}/telemetry", response_model=List[DeviceTelemetryResponse])
def get_device_telemetry(
    device_id: str, days: int = 1, interval_minutes: int = 30, db: Session = Depends(get_db)
):
    cutoff = datetime.utcnow() - timedelta(days=days)
    logs = db.query(DeviceTelemetry).filter(
        DeviceTelemetry.device_id == device_id,
        DeviceTelemetry.timestamp >= cutoff
    ).order_by(DeviceTelemetry.timestamp.desc()).all()
    
    return aggregate_telemetry(logs, interval_minutes=interval_minutes)

@router.get("/device/{device_id}/export")
def export_device_telemetry(
    device_id: str, days: int = 1, interval_minutes: int = 30, db: Session = Depends(get_db)
):
    cutoff = datetime.utcnow() - timedelta(days=days)
    logs = db.query(DeviceTelemetry).filter(
        DeviceTelemetry.device_id == device_id,
        DeviceTelemetry.timestamp >= cutoff
    ).order_by(DeviceTelemetry.timestamp.desc()).all()
    
    aggregated_logs = aggregate_telemetry(logs, interval_minutes=interval_minutes)
    
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Device ID", "Timestamp", "Temperature (C)", "Humidity (%)", "Battery (%)"])
    for log in aggregated_logs:
        writer.writerow([log.device_id, log.timestamp.strftime('%Y-%m-%d %H:%M:%S'), str(log.temperature), str(log.humidity), str(log.battery_level)])
        
    output.seek(0)
    response = StreamingResponse(iter([output.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename=telemetry_{device_id}.csv"
    return response


@router.post("/device/{device_id}/mock", response_model=MessageResponse)
def set_mock_state(
    device_id: str, req: MockControlRequest, db: Session = Depends(get_db)
):
    valid_states = ["normal", "ice", "warm", "failover"]
    if req.mode not in valid_states:
        raise HTTPException(status_code=400, detail=f"Invalid mode. Must be one of {valid_states}")
    
    # 1. Update mock mode in database for this device's sensors
    sensors = db.query(Sensor).filter(Sensor.device_id == device_id, Sensor.active == True).all()
    for s in sensors:
        s.mock_mode = req.mode
    db.commit()
    
    # Generate and insert simulated data immediately for instant UI feedback
    import random
    if req.mode != "failover":
        if req.mode == "ice":
            temp_val = round(random.uniform(-5.0, -2.0), 2)
        elif req.mode == "warm":
            temp_val = round(random.uniform(5.0, 10.0), 2)
        else:
            temp_val = round(random.uniform(2.0, 3.5), 2)
            
        hum_val = round(random.uniform(40.0, 60.0), 2)
        bat_val = 98.0
        
        # 2. Raw Telemetry
        telemetry = DeviceTelemetry(
            device_id=device_id,
            temperature=temp_val,
            humidity=hum_val,
            battery_level=bat_val
        )
        db.add(telemetry)
        
        # 3. Map to logical sensors
        for s in sensors:
            val = temp_val if s.type == "temperature" else hum_val
            reading = SensorReading(sensor_id=s.id, value=val)
            db.add(reading)
            
            # 4. Check thresholds
            if s.max_threshold is not None and val > s.max_threshold:
                recent_alert = db.query(Alert).filter(Alert.sensor_id == s.id, Alert.resolved == False).first()
                if not recent_alert:
                    new_alert = Alert(
                        sensor_id=s.id,
                        value=val,
                        message=f"{s.type.capitalize()} threshold exceeded: {val} > {s.max_threshold}"
                    )
                    db.add(new_alert)
                    
            if s.min_threshold is not None and val < s.min_threshold:
                recent_alert = db.query(Alert).filter(Alert.sensor_id == s.id, Alert.resolved == False).first()
                if not recent_alert:
                    new_alert = Alert(
                        sensor_id=s.id,
                        value=val,
                        message=f"{s.type.capitalize()} threshold dropped below minimum: {val} < {s.min_threshold}"
                    )
                    db.add(new_alert)
        db.commit()
        
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

