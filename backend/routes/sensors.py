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
from backend.schemas import ReadingCreate, ReadingResponse, SensorThresholdUpdate, MessageResponse, DeviceTelemetryResponse, MockControlRequest, DeviceMetrics24hResponse, MonthlyAnalyticsResponse, DailyMetric, BatchContextResponse, DeviceTelemetryHistoryResponse
from backend.models.device_telemetry import DeviceTelemetry
from fastapi.responses import StreamingResponse
import csv
from io import StringIO
from sqlalchemy import func, cast, Date
import calendar

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

def calculate_offline_periods(logs, threshold_minutes: int = 3) -> List[dict]:
    if not logs:
        return []
    
    # Sort logs chronologically (ascending)
    sorted_logs = sorted(logs, key=lambda x: x.timestamp)
    offline_periods = []
    
    for i in range(len(sorted_logs) - 1):
        t1 = sorted_logs[i].timestamp
        t2 = sorted_logs[i+1].timestamp
        diff_mins = (t2 - t1).total_seconds() / 60.0
        
        if diff_mins > threshold_minutes:
            offline_periods.append({
                "start": t1.strftime("%Y-%m-%d %H:%M:%S"),
                "end": t2.strftime("%Y-%m-%d %H:%M:%S"),
                "duration_minutes": int(round(diff_mins))
            })
            
    # Check if currently offline (if last log is older than threshold)
    if sorted_logs:
        last_t = sorted_logs[-1].timestamp
        now = datetime.utcnow()
        diff_now = (now - last_t).total_seconds() / 60.0
        if diff_now > threshold_minutes:
            offline_periods.append({
                "start": last_t.strftime("%Y-%m-%d %H:%M:%S"),
                "end": "Present",
                "duration_minutes": int(round(diff_now))
            })
            
    return offline_periods

def aggregate_plug_telemetry(logs, interval_minutes: int = 30) -> list:
    if not logs:
        return []
    
    # Sort logs ascending by timestamp
    sorted_logs = sorted(logs, key=lambda x: x.timestamp)
    
    # If interval is raw/no-aggregation, return directly
    if interval_minutes <= 1:
        return [
            {
                "id": str(log.id),
                "device_id": log.device_id,
                "timestamp": log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "apower": float(log.apower),
                "voltage": float(log.voltage),
                "current": float(log.current),
                "today_energy": float(log.today_energy),
                "month_energy": float(log.month_energy)
            }
            for log in sorted_logs
        ]
        
    aggregated = []
    current_bucket_start = None
    bucket_apower = []
    bucket_voltage = []
    bucket_current = []
    bucket_today = []
    bucket_month = []
    
    epoch = datetime(1970, 1, 1)
    
    for log in sorted_logs:
        delta = log.timestamp - epoch
        total_minutes = int(delta.total_seconds() // 60)
        rounded_minutes = (total_minutes // interval_minutes) * interval_minutes
        bucket_time = epoch + timedelta(minutes=rounded_minutes)
        
        if current_bucket_start is None:
            current_bucket_start = bucket_time
            
        if bucket_time == current_bucket_start:
            bucket_apower.append(float(log.apower))
            bucket_voltage.append(float(log.voltage))
            bucket_current.append(float(log.current))
            bucket_today.append(float(log.today_energy))
            bucket_month.append(float(log.month_energy))
        else:
            # Save the average for the finished bucket
            avg_apower = sum(bucket_apower) / len(bucket_apower)
            avg_voltage = sum(bucket_voltage) / len(bucket_voltage)
            avg_current = sum(bucket_current) / len(bucket_current)
            max_today = max(bucket_today) if bucket_today else 0.0
            max_month = max(bucket_month) if bucket_month else 0.0
            
            aggregated.append({
                "device_id": sorted_logs[0].device_id,
                "timestamp": current_bucket_start.strftime("%Y-%m-%d %H:%M:%S"),
                "apower": round(avg_apower, 1),
                "voltage": round(avg_voltage, 1),
                "current": round(avg_current, 3),
                "today_energy": round(max_today, 1),
                "month_energy": round(max_month, 1)
            })
            
            # Start new bucket
            current_bucket_start = bucket_time
            bucket_apower = [float(log.apower)]
            bucket_voltage = [float(log.voltage)]
            bucket_current = [float(log.current)]
            bucket_today = [float(log.today_energy)]
            bucket_month = [float(log.month_energy)]
            
    if current_bucket_start is not None:
        avg_apower = sum(bucket_apower) / len(bucket_apower)
        avg_voltage = sum(bucket_voltage) / len(bucket_voltage)
        avg_current = sum(bucket_current) / len(bucket_current)
        max_today = max(bucket_today) if bucket_today else 0.0
        max_month = max(bucket_month) if bucket_month else 0.0
        
        aggregated.append({
            "device_id": sorted_logs[0].device_id,
            "timestamp": current_bucket_start.strftime("%Y-%m-%d %H:%M:%S"),
            "apower": round(avg_apower, 1),
            "voltage": round(avg_voltage, 1),
            "current": round(avg_current, 3),
            "today_energy": round(max_today, 1),
            "month_energy": round(max_month, 1)
        })
        
    return aggregated

@router.get("/device/{device_id}/telemetry", response_model=DeviceTelemetryHistoryResponse)
def get_device_telemetry(
    device_id: str, days: int = 1, interval_minutes: int = 1, db: Session = Depends(get_db)
):
    cutoff = datetime.utcnow() - timedelta(days=days)
    logs = db.query(DeviceTelemetry).filter(
        DeviceTelemetry.device_id == device_id,
        DeviceTelemetry.timestamp >= cutoff
    ).order_by(DeviceTelemetry.timestamp.desc()).all()
    
    # Calculate offline periods using raw logs
    offline_periods = calculate_offline_periods(logs)
    
    # Aggregate telemetry logs
    aggregated_logs = aggregate_telemetry(logs, interval_minutes=interval_minutes)
    
    return DeviceTelemetryHistoryResponse(
        telemetry=aggregated_logs,
        offline_periods=offline_periods
    )

@router.get("/device/{device_id}/metrics/24h", response_model=DeviceMetrics24hResponse)
def get_24h_metrics(device_id: str, db: Session = Depends(get_db)):
    cutoff = datetime.utcnow() - timedelta(hours=24)
    stats = db.query(
        func.avg(DeviceTelemetry.temperature).label('t_avg'),
        func.min(DeviceTelemetry.temperature).label('t_min'),
        func.max(DeviceTelemetry.temperature).label('t_max'),
        func.avg(DeviceTelemetry.humidity).label('h_avg'),
        func.min(DeviceTelemetry.humidity).label('h_min'),
        func.max(DeviceTelemetry.humidity).label('h_max')
    ).filter(
        DeviceTelemetry.device_id == device_id,
        DeviceTelemetry.timestamp >= cutoff
    ).first()
    
    return DeviceMetrics24hResponse(
        device_id=device_id,
        temp_avg=round(stats.t_avg, 2) if stats.t_avg is not None else None,
        temp_min=round(stats.t_min, 2) if stats.t_min is not None else None,
        temp_max=round(stats.t_max, 2) if stats.t_max is not None else None,
        hum_avg=round(stats.h_avg, 2) if stats.h_avg is not None else None,
        hum_min=round(stats.h_min, 2) if stats.h_min is not None else None,
        hum_max=round(stats.h_max, 2) if stats.h_max is not None else None,
    )

@router.get("/device/{device_id}/metrics/monthly", response_model=MonthlyAnalyticsResponse)
def get_monthly_analytics(
    device_id: str, 
    year: int = None, 
    month: int = None, 
    db: Session = Depends(get_db)
):
    now = datetime.utcnow()
    year = year or now.year
    month = month or now.month
    
    num_days = calendar.monthrange(year, month)[1]
    start_date = datetime(year, month, 1)
    end_date = datetime(year, month, num_days, 23, 59, 59)
    
    stats = db.query(
        cast(DeviceTelemetry.timestamp, Date).label('day'),
        func.min(DeviceTelemetry.temperature).label('t_min'),
        func.max(DeviceTelemetry.temperature).label('t_max'),
        func.min(DeviceTelemetry.humidity).label('h_min'),
        func.max(DeviceTelemetry.humidity).label('h_max')
    ).filter(
        DeviceTelemetry.device_id == device_id,
        DeviceTelemetry.timestamp >= start_date,
        DeviceTelemetry.timestamp <= end_date
    ).group_by(
        cast(DeviceTelemetry.timestamp, Date)
    ).order_by(
        cast(DeviceTelemetry.timestamp, Date)
    ).all()
    
    daily_metrics = []
    for row in stats:
        daily_metrics.append(DailyMetric(
            date=row.day.strftime("%Y-%m-%d"),
            temp_min=round(row.t_min, 2) if row.t_min is not None else None,
            temp_max=round(row.t_max, 2) if row.t_max is not None else None,
            hum_min=round(row.h_min, 2) if row.h_min is not None else None,
            hum_max=round(row.h_max, 2) if row.h_max is not None else None,
        ))
        
    return MonthlyAnalyticsResponse(
        device_id=device_id,
        year=year,
        month=month,
        daily_metrics=daily_metrics
    )

@router.get("/device/{device_id}/metrics/rolling", response_model=MonthlyAnalyticsResponse)
def get_rolling_analytics(
    device_id: str,
    days: int = 30,
    db: Session = Depends(get_db)
):
    """
    Fetch rolling daily metrics (min/max temperature and humidity) for the last N days.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    stats = db.query(
        cast(DeviceTelemetry.timestamp, Date).label('day'),
        func.min(DeviceTelemetry.temperature).label('t_min'),
        func.max(DeviceTelemetry.temperature).label('t_max'),
        func.min(DeviceTelemetry.humidity).label('h_min'),
        func.max(DeviceTelemetry.humidity).label('h_max')
    ).filter(
        DeviceTelemetry.device_id == device_id,
        DeviceTelemetry.timestamp >= cutoff
    ).group_by(
        cast(DeviceTelemetry.timestamp, Date)
    ).order_by(
        cast(DeviceTelemetry.timestamp, Date)
    ).all()
    
    daily_metrics = []
    for row in stats:
        daily_metrics.append(DailyMetric(
            date=row.day.strftime("%Y-%m-%d"),
            temp_min=round(row.t_min, 2) if row.t_min is not None else None,
            temp_max=round(row.t_max, 2) if row.t_max is not None else None,
            hum_min=round(row.h_min, 2) if row.h_min is not None else None,
            hum_max=round(row.h_max, 2) if row.h_max is not None else None,
        ))
        
    return MonthlyAnalyticsResponse(
        device_id=device_id,
        year=datetime.utcnow().year,
        month=datetime.utcnow().month,
        daily_metrics=daily_metrics
    )

@router.get("/device/{device_id}/export")
def export_device_telemetry(
    device_id: str, days: int = 1, interval_minutes: int = 1, db: Session = Depends(get_db)
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
            "alert_webhook_url": s.alert_webhook_url,
            "recovery_webhook_url": s.recovery_webhook_url,
            "tapo_ip": s.tapo_ip,
            "tapo_username": s.tapo_username,
            "tapo_password": s.tapo_password,
            "tapo_billing_rate": float(s.tapo_billing_rate) if s.tapo_billing_rate is not None else None,
        }
        for s in sensors
    ]

@router.put("/device/{device_id}/thresholds")
def update_device_thresholds(device_id: str, req: dict, db: Session = Depends(get_db)):
    """Public endpoint: update thresholds, webhooks, and Tapo settings for a device's sensors."""
    sensors = db.query(Sensor).filter(Sensor.device_id == device_id, Sensor.active == True).all()
    if not sensors:
        raise HTTPException(status_code=404, detail="Device not found")
    for s in sensors:
        if s.type == "temperature":
            if "temp_min" in req:
                s.min_threshold = req["temp_min"]
            if "temp_max" in req:
                s.max_threshold = req["temp_max"]
            if "temp_alert_webhook_url" in req:
                s.alert_webhook_url = req["temp_alert_webhook_url"]
            if "temp_recovery_webhook_url" in req:
                s.recovery_webhook_url = req["temp_recovery_webhook_url"]
            if "temp_tapo_ip" in req:
                s.tapo_ip = req["temp_tapo_ip"]
            if "temp_tapo_username" in req:
                s.tapo_username = req["temp_tapo_username"]
            if "temp_tapo_password" in req:
                s.tapo_password = req["temp_tapo_password"]
            if "temp_tapo_billing_rate" in req:
                s.tapo_billing_rate = req["temp_tapo_billing_rate"]
        elif s.type == "humidity":
            if "hum_min" in req:
                s.min_threshold = req["hum_min"]
            if "hum_max" in req:
                s.max_threshold = req["hum_max"]
            if "hum_alert_webhook_url" in req:
                s.alert_webhook_url = req["hum_alert_webhook_url"]
            if "hum_recovery_webhook_url" in req:
                s.recovery_webhook_url = req["hum_recovery_webhook_url"]
            if "hum_tapo_ip" in req:
                s.tapo_ip = req["hum_tapo_ip"]
            if "hum_tapo_username" in req:
                s.tapo_username = req["hum_tapo_username"]
            if "hum_tapo_password" in req:
                s.tapo_password = req["hum_tapo_password"]
            if "hum_tapo_billing_rate" in req:
                s.tapo_billing_rate = req["hum_tapo_billing_rate"]
    db.commit()
    return {"message": "Thresholds, webhooks, and Tapo configurations updated"}

@router.get("/device/{device_id}/plug")
async def get_device_plug_status(device_id: str, db: Session = Depends(get_db)):
    """Dynamic endpoint to fetch plug telemetry (voltage, current, power) from Tapo.
    Falls back to last known DB record when direct LAN connection is unavailable (e.g. cloud deployment).
    """
    sensor = db.query(Sensor).filter(
        Sensor.device_id == device_id,
        Sensor.type == "temperature",
        Sensor.active == True
    ).first()

    # If Tapo config exists, try to query it directly (works on local LAN)
    if sensor and sensor.tapo_ip and sensor.tapo_username and sensor.tapo_password:
        rate = float(sensor.tapo_billing_rate) if sensor.tapo_billing_rate is not None else 10.0
        
        try:
            from backend.services.tapo import get_tapo_telemetry_cached
            telemetry = await get_tapo_telemetry_cached(
                sensor.tapo_ip, sensor.tapo_username, sensor.tapo_password, device_id
            )
            today_kwh = telemetry.get("today_energy", 0.0) / 1000.0
            month_kwh = telemetry.get("month_energy", 0.0) / 1000.0
            
            return {
                **telemetry,
                "today_kwh": round(today_kwh, 3),
                "month_kwh": round(month_kwh, 3),
                "today_bill": round(today_kwh * rate, 2),
                "month_bill": round(month_kwh * rate, 2),
                "billing_rate": rate,
                "supported": True,
                "type": "tapo"
            }
        except Exception as e:
            import logging
            log = logging.getLogger(__name__)
            log.error(f"Direct Tapo connection failed for {device_id} ({sensor.tapo_ip}): {e}")
            
            # Fallback: serve the most recent PlugTelemetry record stored by the background worker.
            # The worker runs on the same LAN as the plug so it CAN reach it. The API server
            # on a cloud host (e.g. Render) cannot reach a local-network IP, so we serve the
            # last logged value instead of returning zeros / OFFLINE.
            from backend.models.plug_telemetry import PlugTelemetry
            last_log = db.query(PlugTelemetry).filter(
                PlugTelemetry.device_id == device_id
            ).order_by(PlugTelemetry.timestamp.desc()).first()
            
            if last_log:
                today_kwh = float(last_log.today_energy) / 1000.0
                month_kwh = float(last_log.month_energy) / 1000.0
                log.info(f"Serving last DB plug record for {device_id} logged at {last_log.timestamp}")
                return {
                    "state": "unknown",
                    "voltage": float(last_log.voltage),
                    "current": float(last_log.current),
                    "apower": float(last_log.apower),
                    "today_energy": float(last_log.today_energy),
                    "month_energy": float(last_log.month_energy),
                    "today_kwh": round(today_kwh, 3),
                    "month_kwh": round(month_kwh, 3),
                    "today_bill": round(today_kwh * rate, 2),
                    "month_bill": round(month_kwh * rate, 2),
                    "billing_rate": rate,
                    "supported": True,
                    "type": "tapo",
                    "last_known": True,
                    "last_known_at": last_log.timestamp.strftime("%Y-%m-%d %H:%M UTC")
                }
            
            # No DB records at all — device was never reachable
            return {
                "state": "offline",
                "voltage": 0.0,
                "current": 0.0,
                "apower": 0.0,
                "today_energy": 0.0,
                "month_energy": 0.0,
                "today_kwh": 0.0,
                "month_kwh": 0.0,
                "today_bill": 0.0,
                "month_bill": 0.0,
                "billing_rate": float(sensor.tapo_billing_rate) if sensor.tapo_billing_rate is not None else 10.0,
                "supported": True,
                "type": "tapo",
                "error": str(e)
            }

    return {
        "state": "off",
        "voltage": 0.0,
        "current": 0.0,
        "apower": 0.0,
        "supported": False
    }

@router.get("/device/{device_id}/plug/history")
def get_plug_telemetry_history(
    device_id: str, days: int = 1, interval_minutes: int = 1, db: Session = Depends(get_db)
):
    """Fetch plug telemetry history for charts."""
    from backend.models.plug_telemetry import PlugTelemetry
    cutoff = datetime.utcnow() - timedelta(days=days)
    logs = db.query(PlugTelemetry).filter(
        PlugTelemetry.device_id == device_id,
        PlugTelemetry.timestamp >= cutoff
    ).order_by(PlugTelemetry.timestamp.asc()).all()  # Sort ascending for charts
    
    # Calculate offline periods using raw logs
    offline_periods = calculate_offline_periods(logs)
    
    # Aggregate plug telemetry logs
    aggregated_logs = aggregate_plug_telemetry(logs, interval_minutes=interval_minutes)
    
    return {
        "history": aggregated_logs,
        "offline_periods": offline_periods
    }

@router.get("/device/{device_id}/plug/export")
def export_plug_telemetry(
    device_id: str, days: int = 1, interval_minutes: int = 1, db: Session = Depends(get_db)
):
    """Export plug telemetry logs as a CSV file."""
    from backend.models.plug_telemetry import PlugTelemetry
    cutoff = datetime.utcnow() - timedelta(days=days)
    logs = db.query(PlugTelemetry).filter(
        PlugTelemetry.device_id == device_id,
        PlugTelemetry.timestamp >= cutoff
    ).order_by(PlugTelemetry.timestamp.desc()).all()
    
    # Export aggregated logs
    aggregated_logs = aggregate_plug_telemetry(logs, interval_minutes=interval_minutes)
    
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Device ID", "Timestamp", "Active Power (W)", "Voltage (V)", "Current (A)", "Today Energy (Wh)", "Month Energy (Wh)"])
    for log in aggregated_logs:
        writer.writerow([
            log["device_id"],
            log["timestamp"],
            str(log["apower"]),
            str(log["voltage"]),
            str(log["current"]),
            str(log["today_energy"]),
            str(log["month_energy"])
        ])
        
    output.seek(0)
    response = StreamingResponse(iter([output.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename=plug_telemetry_{device_id}.csv"
    return response

@router.post("/device/{device_id}/plug/toggle")
async def toggle_device_plug(device_id: str, req: dict, db: Session = Depends(get_db)):
    """Dynamic endpoint to toggle plug state (ON/OFF) via Tapo API."""
    state = req.get("state")
    if not state or state not in ["on", "off"]:
        raise HTTPException(status_code=400, detail="Invalid state. Must be 'on' or 'off'.")
    
    sensor = db.query(Sensor).filter(
        Sensor.device_id == device_id,
        Sensor.type == "temperature",
        Sensor.active == True
    ).first()

    # If Tapo config exists, control it directly
    if sensor and sensor.tapo_ip and sensor.tapo_username and sensor.tapo_password:
        try:
            from backend.services.tapo import tapo_control_async
            success = await tapo_control_async(
                sensor.tapo_ip, sensor.tapo_username, sensor.tapo_password, state, device_id=device_id
            )
            if success:
                return {"message": f"Plug turned {state}", "supported": True, "type": "tapo"}
            else:
                raise HTTPException(status_code=500, detail="Failed to control Tapo plug.")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Tapo service error: {e}")

    raise HTTPException(status_code=501, detail="Plug control service is not configured for this device.")

@router.get("/device/{device_id}/batch-context", response_model=BatchContextResponse)
def get_batch_context(
    device_id: str,
    start_time: datetime,
    end_time: datetime,
    db: Session = Depends(get_db)
):
    """
    Fetch exact historical data for the timeframe a batch was inside this room/fridge.
    Provides total averages, min, max, and all the raw telemetry logs during that time.
    """
    logs = db.query(DeviceTelemetry).filter(
        DeviceTelemetry.device_id == device_id,
        DeviceTelemetry.timestamp >= start_time,
        DeviceTelemetry.timestamp <= end_time
    ).order_by(DeviceTelemetry.timestamp.desc()).all()
    
    stats = db.query(
        func.avg(DeviceTelemetry.temperature).label('t_avg'),
        func.min(DeviceTelemetry.temperature).label('t_min'),
        func.max(DeviceTelemetry.temperature).label('t_max'),
        func.avg(DeviceTelemetry.humidity).label('h_avg'),
        func.min(DeviceTelemetry.humidity).label('h_min'),
        func.max(DeviceTelemetry.humidity).label('h_max')
    ).filter(
        DeviceTelemetry.device_id == device_id,
        DeviceTelemetry.timestamp >= start_time,
        DeviceTelemetry.timestamp <= end_time
    ).first()
    
    # Send aggregated logs (30min) instead of strictly raw if the timeframe is long
    aggregated_logs = aggregate_telemetry(logs, interval_minutes=30)
    
    return BatchContextResponse(
        device_id=device_id,
        start_time=start_time,
        end_time=end_time,
        temp_avg=round(stats.t_avg, 2) if stats.t_avg is not None else None,
        temp_min=round(stats.t_min, 2) if stats.t_min is not None else None,
        temp_max=round(stats.t_max, 2) if stats.t_max is not None else None,
        hum_avg=round(stats.h_avg, 2) if stats.h_avg is not None else None,
        hum_min=round(stats.h_min, 2) if stats.h_min is not None else None,
        hum_max=round(stats.h_max, 2) if stats.h_max is not None else None,
        telemetry_logs=aggregated_logs
    )

