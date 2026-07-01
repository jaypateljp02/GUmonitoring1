"""Sensor routes with auto-alert logic."""
from typing import List, Optional
from datetime import datetime, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
import os

def verify_edge_api_key(x_api_key: str = Header(...)):
    from backend.config import EDGE_API_KEY
    if x_api_key != EDGE_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return x_api_key

from backend.database import get_db
from backend.models.sensor import Sensor
from backend.models.reading import SensorReading
from backend.models.alert import Alert
from backend.middleware.jwt_verify import get_current_user, require_admin, TokenUser
from backend.schemas import ReadingCreate, ReadingResponse, SensorThresholdUpdate, MessageResponse, DeviceTelemetryResponse, MockControlRequest, DeviceMetrics24hResponse, MonthlyAnalyticsResponse, DailyMetric, BatchContextResponse, DeviceTelemetryHistoryResponse, PlugMetrics24hResponse
from backend.models.device_telemetry import DeviceTelemetry
from fastapi.responses import StreamingResponse
import csv
from io import StringIO
from sqlalchemy import func, cast, Date
import calendar

def _to_kwh(val) -> float:
    if val is None:
        return 0.0
    f_val = float(val)
    if f_val > 100.0:
        return f_val / 1000.0
    return f_val

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
    is_alert_enabled = not (sensor.min_threshold == 0 and sensor.max_threshold == 0)
    if is_alert_enabled:
        if sensor.min_threshold is not None and req.value < sensor.min_threshold:
            alert_msg = f"[{sensor.name}] {sensor.type.capitalize()} too low: {req.value} (Min: {sensor.min_threshold})"
        elif sensor.max_threshold is not None and req.value > sensor.max_threshold:
            alert_msg = f"[{sensor.name}] {sensor.type.capitalize()} too high: {req.value} (Max: {sensor.max_threshold})"

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


def aggregate_telemetry(db: Session, device_id: str, start_time: datetime, end_time: datetime, interval_minutes: int) -> List[DeviceTelemetryResponse]:
    from sqlalchemy import text
    from decimal import Decimal
    import uuid
    
    if interval_minutes <= 1:
        logs = db.query(DeviceTelemetry).filter(
            DeviceTelemetry.device_id == device_id,
            DeviceTelemetry.timestamp >= start_time,
            DeviceTelemetry.timestamp <= end_time
        ).order_by(DeviceTelemetry.timestamp.desc()).all()
        return [DeviceTelemetryResponse.model_validate(log) for log in logs]
        
    interval_seconds = interval_minutes * 60
    agg_query = text('''
        SELECT
            to_timestamp(floor(extract(epoch from timestamp) / :interval) * :interval) as bucket,
            AVG(temperature) as temperature,
            AVG(humidity) as humidity,
            AVG(battery_level) as battery_level
        FROM monitoring.device_telemetry
        WHERE device_id = :device_id AND timestamp >= :start_time AND timestamp <= :end_time
        GROUP BY bucket
        ORDER BY bucket DESC
    ''')
    agg_results = db.execute(agg_query, {"device_id": device_id, "start_time": start_time, "end_time": end_time, "interval": interval_seconds}).fetchall()
    
    return [
        DeviceTelemetryResponse(
            id=uuid.uuid4(),
            device_id=device_id,
            timestamp=row.bucket,
            temperature=Decimal(str(round(row.temperature, 2))) if row.temperature else Decimal(0),
            humidity=Decimal(str(round(row.humidity, 2))) if row.humidity else Decimal(0),
            battery_level=Decimal(str(round(row.battery_level, 2))) if row.battery_level else Decimal(0)
        )
        for row in agg_results
    ]

def calculate_offline_periods(db: Session, table_name: str, device_id: str, start_time: datetime, threshold_minutes: int = 3) -> List[dict]:
    from sqlalchemy import text
    
    offline_query = text(f'''
        SELECT 
            start_t as start, 
            end_t as end, 
            duration_minutes 
        FROM (
            SELECT 
                lag(timestamp) OVER (ORDER BY timestamp ASC) as start_t,
                timestamp as end_t,
                EXTRACT(EPOCH FROM (timestamp - lag(timestamp) OVER (ORDER BY timestamp ASC)))/60.0 as duration_minutes
            FROM monitoring.{table_name}
            WHERE device_id = :device_id AND timestamp >= :start_time
        ) as gaps
        WHERE duration_minutes > :threshold
    ''')
    offline_results = db.execute(offline_query, {"device_id": device_id, "start_time": start_time, "threshold": float(threshold_minutes)}).fetchall()
    
    offline_periods = [{
        "start": row.start.strftime("%Y-%m-%d %H:%M:%S"),
        "end": row.end.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_minutes": int(round(row.duration_minutes))
    } for row in offline_results]
    
    latest_query = text(f"SELECT timestamp FROM monitoring.{table_name} WHERE device_id = :device_id ORDER BY timestamp DESC LIMIT 1")
    latest = db.execute(latest_query, {"device_id": device_id}).first()
    if latest:
        diff_now = (datetime.utcnow() - latest[0]).total_seconds() / 60.0
        if diff_now > threshold_minutes:
            offline_periods.append({
                "start": latest[0].strftime("%Y-%m-%d %H:%M:%S"),
                "end": "Present",
                "duration_minutes": int(round(diff_now))
            })
            
    return offline_periods

def aggregate_plug_telemetry(db: Session, device_id: str, start_time: datetime, end_time: datetime, interval_minutes: int) -> list:
    from sqlalchemy import text
    if interval_minutes <= 1:
        from backend.models.plug_telemetry import PlugTelemetry
        logs = db.query(PlugTelemetry).filter(
            PlugTelemetry.device_id == device_id,
            PlugTelemetry.timestamp >= start_time,
            PlugTelemetry.timestamp <= end_time
        ).order_by(PlugTelemetry.timestamp.desc()).all()
        return [{
            "id": str(log.id),
            "device_id": log.device_id,
            "timestamp": log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "apower": float(log.apower),
            "voltage": float(log.voltage),
            "current": float(log.current),
            "today_energy": float(log.today_energy),
            "month_energy": float(log.month_energy)
        } for log in logs]
        
    interval_seconds = interval_minutes * 60
    agg_query = text('''
        SELECT
            to_timestamp(floor(extract(epoch from timestamp) / :interval) * :interval) as bucket,
            AVG(apower) as apower,
            AVG(voltage) as voltage,
            AVG(current) as current,
            MAX(today_energy) as today_energy,
            MAX(month_energy) as month_energy
        FROM monitoring.plug_telemetry
        WHERE device_id = :device_id AND timestamp >= :start_time AND timestamp <= :end_time
        GROUP BY bucket
        ORDER BY bucket DESC
    ''')
    agg_results = db.execute(agg_query, {"device_id": device_id, "start_time": start_time, "end_time": end_time, "interval": interval_seconds}).fetchall()
    
    return [{
        "device_id": device_id,
        "timestamp": row.bucket.strftime("%Y-%m-%d %H:%M:%S"),
        "apower": round(row.apower, 1) if row.apower else 0.0,
        "voltage": round(row.voltage, 1) if row.voltage else 0.0,
        "current": round(row.current, 3) if row.current else 0.0,
        "today_energy": round(row.today_energy, 1) if row.today_energy else 0.0,
        "month_energy": round(row.month_energy, 1) if row.month_energy else 0.0
    } for row in agg_results]

@router.get("/device/{device_id}/telemetry", response_model=DeviceTelemetryHistoryResponse)
def get_device_telemetry(
    device_id: str, days: int = 1, interval_minutes: int = 1, db: Session = Depends(get_db)
):
    cutoff = datetime.utcnow() - timedelta(days=days)
    end_time = datetime.utcnow()
    
    offline_periods = calculate_offline_periods(db, "device_telemetry", device_id, cutoff)
    aggregated_logs = aggregate_telemetry(db, device_id, cutoff, end_time, interval_minutes)
    
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
    
    end_time = datetime.utcnow()
    aggregated_logs = aggregate_telemetry(db, device_id, cutoff, end_time, interval_minutes)
    
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
            "tapo_running_threshold": float(s.tapo_running_threshold) if s.tapo_running_threshold is not None else 80.0,
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
            if "temp_tapo_running_threshold" in req:
                s.tapo_running_threshold = req["temp_tapo_running_threshold"]
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
            if "hum_tapo_running_threshold" in req:
                s.tapo_running_threshold = req["hum_tapo_running_threshold"]
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
            import asyncio
            telemetry = await asyncio.wait_for(get_tapo_telemetry_cached(
                sensor.tapo_ip, sensor.tapo_username, sensor.tapo_password, device_id
            ), timeout=1.5)
            if telemetry.get("state") == "offline" or "error" in telemetry:
                raise Exception(telemetry.get("error") or "Tapo plug is offline")
            today_kwh = _to_kwh(telemetry.get("today_energy", 0.0))
            month_kwh = _to_kwh(telemetry.get("month_energy", 0.0))
            
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
                today_kwh = _to_kwh(last_log.today_energy)
                month_kwh = _to_kwh(last_log.month_energy)
                
                # Check if telemetry is older than 3 minutes (180 seconds)
                is_stale = (datetime.utcnow() - last_log.timestamp).total_seconds() > 180.0
                if is_stale:
                    log.warning(f"Tapo telemetry for {device_id} is stale (last seen {last_log.timestamp}). Marking offline.")
                    return {
                        "state": "offline",
                        "voltage": 0.0,
                        "current": 0.0,
                        "apower": 0.0,
                        "today_energy": float(last_log.today_energy),
                        "month_energy": float(last_log.month_energy),
                        "today_kwh": round(today_kwh, 3),
                        "month_kwh": round(month_kwh, 3),
                        "today_bill": round(today_kwh * rate, 2),
                        "month_bill": round(month_kwh * rate, 2),
                        "billing_rate": rate,
                        "supported": True,
                        "type": "tapo",
                        "error": f"Plug is disconnected (offline since {last_log.timestamp.strftime('%Y-%m-%d %H:%M UTC')})"
                    }
                
                log.info(f"Serving last DB plug record for {device_id} logged at {last_log.timestamp}")
                return {
                    "state": "on" if float(last_log.apower) > 0.5 else "off",
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
                    "last_known": False,
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

@router.get("/device/{device_id}/plug/metrics/24h", response_model=PlugMetrics24hResponse)
def get_plug_24h_metrics(device_id: str, db: Session = Depends(get_db)):
    """Get 24-hour min/max/avg metrics for plug telemetry, weekly baselines, and status diagnosis."""
    from backend.models.plug_telemetry import PlugTelemetry
    from backend.models.device_telemetry import DeviceTelemetry
    from collections import defaultdict
    
    # 1. Get the temperature sensor and running threshold
    sensor = db.query(Sensor).filter(
        Sensor.device_id == device_id,
        Sensor.type == "temperature",
        Sensor.active == True
    ).first()
    
    threshold = 80.0
    if sensor and sensor.tapo_running_threshold is not None:
        threshold = float(sensor.tapo_running_threshold)

    # 2. Get 24-hour min/max/avg raw stats
    cutoff_24h = datetime.utcnow() - timedelta(hours=24)
    stats = db.query(
        func.avg(PlugTelemetry.apower).label('p_avg'),
        func.min(PlugTelemetry.apower).label('p_min'),
        func.max(PlugTelemetry.apower).label('p_max'),
        func.avg(PlugTelemetry.voltage).label('v_avg'),
        func.min(PlugTelemetry.voltage).label('v_min'),
        func.max(PlugTelemetry.voltage).label('v_max'),
        func.avg(PlugTelemetry.current).label('c_avg'),
        func.min(PlugTelemetry.current).label('c_min'),
        func.max(PlugTelemetry.current).label('c_max'),
        func.max(PlugTelemetry.today_energy).label('energy_max'),
    ).filter(
        PlugTelemetry.device_id == device_id,
        PlugTelemetry.timestamp >= cutoff_24h
    ).first()

    energy_kwh = round(_to_kwh(stats.energy_max), 3) if (stats and stats.energy_max is not None) else 0.0

    # 3. Calculate last 24h Use Time (runtime) and On/Off Cycles
    logs_24h = db.query(PlugTelemetry).filter(
        PlugTelemetry.device_id == device_id,
        PlugTelemetry.timestamp >= cutoff_24h
    ).order_by(PlugTelemetry.timestamp.asc()).all()

    runtime_hours_24h = 0.0
    starts_count_24h = 0
    was_running = False
    
    for i, log in enumerate(logs_24h):
        curr_running = float(log.apower) >= threshold
        if curr_running and not was_running:
            starts_count_24h += 1
        was_running = curr_running
        
        if i > 0 and curr_running:
            delta = (log.timestamp - logs_24h[i-1].timestamp).total_seconds() / 3600.0
            if delta < 0.25:  # Limit gap to 15 mins to ignore offline gaps
                runtime_hours_24h += delta

    duty_cycle_pct_24h = round((runtime_hours_24h / 24.0) * 100.0, 1) if runtime_hours_24h > 0 else 0.0

    # 4. Calculate 7-Day Baseline Averages (Weekly average daily runtime, starts, and energy)
    cutoff_7d = datetime.utcnow() - timedelta(days=7)
    logs_7d = db.query(PlugTelemetry).filter(
        PlugTelemetry.device_id == device_id,
        PlugTelemetry.timestamp >= cutoff_7d
    ).order_by(PlugTelemetry.timestamp.asc()).all()

    daily_logs = defaultdict(list)
    for log in logs_7d:
        day_str = log.timestamp.strftime("%Y-%m-%d")
        daily_logs[day_str].append(log)

    daily_runtimes = []
    daily_starts = []
    daily_energies = []

    for day_str, day_logs in daily_logs.items():
        day_logs_sorted = sorted(day_logs, key=lambda x: x.timestamp)
        day_runtime = 0.0
        day_starts_count = 0
        day_was_running = False
        
        for j, log in enumerate(day_logs_sorted):
            curr_running = float(log.apower) >= threshold
            if curr_running and not day_was_running:
                day_starts_count += 1
            day_was_running = curr_running
            
            if j > 0 and curr_running:
                delta = (log.timestamp - day_logs_sorted[j-1].timestamp).total_seconds() / 3600.0
                if delta < 0.25:
                    day_runtime += delta
                    
        daily_runtimes.append(day_runtime)
        daily_starts.append(day_starts_count)
        
        max_energy = max(float(x.today_energy) for x in day_logs) if day_logs else 0.0
        daily_energies.append(max_energy / 1000.0)

    num_days = len(daily_logs) if daily_logs else 1
    runtime_hours_avg_7d = round(sum(daily_runtimes) / num_days, 2)
    starts_count_avg_7d = round(sum(daily_starts) / num_days, 1)
    energy_kwh_avg_7d = round(sum(daily_energies) / num_days, 3)

    # 5. Determine Compressor State
    compressor_state = "idle"
    latest_log = logs_24h[-1] if logs_24h else None
    if latest_log:
        # If no log in last 10 minutes, treat as offline
        if datetime.utcnow() - latest_log.timestamp > timedelta(minutes=10):
            compressor_state = "offline"
        elif float(latest_log.apower) >= threshold:
            compressor_state = "running"

    # 6. Diagnosis and Smart Warnings
    diagnosis = "healthy"
    abnormal_flags = []
    observation_msg = "Observation: Temperature and compressor running cycles are normal."
    
    current_temp = None
    latest_temp_log = db.query(DeviceTelemetry).filter(
        DeviceTelemetry.device_id == device_id
    ).order_by(DeviceTelemetry.timestamp.desc()).first()
    
    if latest_temp_log and datetime.utcnow() - latest_temp_log.timestamp <= timedelta(minutes=15):
        current_temp = float(latest_temp_log.temperature)

    temp_high = False
    if sensor and sensor.max_threshold is not None and current_temp is not None:
        if current_temp > float(sensor.max_threshold):
            temp_high = True

    if temp_high:
        if compressor_state == "running":
            diagnosis = "cooling_fail"
            observation_msg = f"Observation: Temp is high ({current_temp:.1f}°C > {float(sensor.max_threshold):.1f}°C) while the compressor is actively running (average: {round(stats.p_avg, 0) if (stats and stats.p_avg is not None) else 0.0} W)."
            abnormal_flags.append("cooling_failure")
        elif compressor_state == "idle":
            diagnosis = "power_issue"
            observation_msg = f"Observation: Temp is high ({current_temp:.1f}°C > {float(sensor.max_threshold):.1f}°C) but compressor has been idle/off (average: {round(stats.p_avg, 0) if (stats and stats.p_avg is not None) else 0.0} W)."
            abnormal_flags.append("power_issue")
    else:
        # Check for Inefficiency (Stable temp but 24h runtime is 25% higher than weekly average)
        if runtime_hours_24h > 1.25 * runtime_hours_avg_7d and runtime_hours_avg_7d > 0.5:
            diagnosis = "inefficient"
            observation_msg = f"Observation: Temperature is stable, but 24h runtime ({runtime_hours_24h:.1f} hrs) is 25%+ higher than weekly average ({runtime_hours_avg_7d:.1f} hrs), indicating early inefficiency."
            abnormal_flags.append("inefficiency")

    # Check for sudden Spikes compared to weekly baseline
    if runtime_hours_24h > 1.5 * runtime_hours_avg_7d and runtime_hours_avg_7d > 0.5:
        abnormal_flags.append("runtime_spike")
    if starts_count_24h > 1.5 * starts_count_avg_7d and starts_count_avg_7d > 2:
        abnormal_flags.append("starts_spike")
    if energy_kwh > 1.5 * energy_kwh_avg_7d and energy_kwh_avg_7d > 0.1:
        abnormal_flags.append("energy_spike")

    if ("runtime_spike" in abnormal_flags or "starts_spike" in abnormal_flags or "energy_spike" in abnormal_flags) and diagnosis == "healthy":
        observation_msg = f"Observation: Abnormal activity detected. Today's runtime ({runtime_hours_24h:.1f}h) or cycles ({starts_count_24h}) have spiked compared to 7-day averages."

    return PlugMetrics24hResponse(
        device_id=device_id,
        power_avg=round(stats.p_avg, 2) if (stats and stats.p_avg is not None) else None,
        power_min=round(stats.p_min, 2) if (stats and stats.p_min is not None) else None,
        power_max=round(stats.p_max, 2) if (stats and stats.p_max is not None) else None,
        voltage_avg=round(stats.v_avg, 1) if (stats and stats.v_avg is not None) else None,
        voltage_min=round(stats.v_min, 1) if (stats and stats.v_min is not None) else None,
        voltage_max=round(stats.v_max, 1) if (stats and stats.v_max is not None) else None,
        current_avg=round(stats.c_avg, 3) if (stats and stats.c_avg is not None) else None,
        current_min=round(stats.c_min, 3) if (stats and stats.c_min is not None) else None,
        current_max=round(stats.c_max, 3) if (stats and stats.c_max is not None) else None,
        energy_total_kwh=energy_kwh,
        runtime_hours_24h=round(runtime_hours_24h, 2),
        duty_cycle_pct_24h=duty_cycle_pct_24h,
        starts_count_24h=starts_count_24h,
        runtime_hours_avg_7d=runtime_hours_avg_7d,
        starts_count_avg_7d=starts_count_avg_7d,
        energy_kwh_avg_7d=energy_kwh_avg_7d,
        compressor_state=compressor_state,
        diagnosis=diagnosis,
        abnormal_flags=abnormal_flags,
        observation_msg=observation_msg
    )

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
    offline_periods = calculate_offline_periods(db, "plug_telemetry", device_id, cutoff)
    
    # Aggregate plug telemetry logs
    end_time = datetime.utcnow()
    aggregated_logs = aggregate_plug_telemetry(db, device_id, cutoff, end_time, interval_minutes)
    
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
    end_time = datetime.utcnow()
    aggregated_logs = aggregate_plug_telemetry(db, device_id, cutoff, end_time, interval_minutes)
    
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
    """Dynamic endpoint to queue a Tapo plug state (ON/OFF) toggle request."""
    state = req.get("state")
    if not state or state not in ["on", "off"]:
        raise HTTPException(status_code=400, detail="Invalid state. Must be 'on' or 'off'.")
    
    sensor = db.query(Sensor).filter(
        Sensor.device_id == device_id,
        Sensor.type == "temperature",
        Sensor.active == True
    ).first()

    if not (sensor and sensor.tapo_ip and sensor.tapo_username and sensor.tapo_password):
        raise HTTPException(status_code=501, detail="Plug control service is not configured for this device.")

    from backend.models.plug_command import PlugCommand
    
    # Cancel any existing pending commands for this device to prevent backlog
    db.query(PlugCommand).filter(
        PlugCommand.device_id == device_id,
        PlugCommand.status == "pending"
    ).update({"status": "cancelled"})
    
    cmd = PlugCommand(
        device_id=device_id,
        command=state,
        status="pending",
        created_at=datetime.utcnow()
    )
    db.add(cmd)
    db.commit()
    
    return {"message": "Tapo plug toggle command queued successfully", "supported": True, "type": "tapo", "status": "pending"}

@router.get("/tapo/commands", tags=["Edge"])
def get_pending_tapo_commands(
    db: Session = Depends(get_db), 
    api_key: str = Depends(verify_edge_api_key)
):
    """Fetch all pending Tapo commands for the edge script to execute."""
    from backend.models.plug_command import PlugCommand
    
    commands = db.query(PlugCommand).filter(
        PlugCommand.status == "pending"
    ).all()
    
    res = []
    for cmd in commands:
        cmd.status = "executing"
        res.append({
            "command_id": str(cmd.id),
            "device_id": cmd.device_id,
            "command": cmd.command
        })
    db.commit()
    return res

from pydantic import BaseModel
class CommandStatusRequest(BaseModel):
    status: str  # 'done' or 'failed'
    error: Optional[str] = None

@router.post("/tapo/commands/{command_id}/status", tags=["Edge"])
def update_tapo_command_status(
    command_id: str,
    req: CommandStatusRequest,
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_edge_api_key)
):
    """Update execution status of a Tapo command."""
    from backend.models.plug_command import PlugCommand
    
    cmd = db.query(PlugCommand).filter(PlugCommand.id == command_id).first()
    if not cmd:
        raise HTTPException(status_code=404, detail="Command not found")
        
    cmd.status = req.status
    cmd.executed_at = datetime.utcnow()
    if req.error:
        cmd.error = req.error
        
    db.commit()
    return {"message": "Command status updated successfully"}

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
    aggregated_logs = aggregate_telemetry(db, device_id, start_time, end_time, 30)
    
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


@router.get("/tapo/configs", tags=["Edge"])
def get_tapo_configs(db: Session = Depends(get_db), api_key: str = Depends(verify_edge_api_key)):
    """Fetch all Tapo configurations for the edge script to poll."""
    sensors = db.query(Sensor).filter(
        Sensor.active == True,
        Sensor.tapo_ip.isnot(None),
        Sensor.tapo_username.isnot(None),
        Sensor.tapo_password.isnot(None)
    ).all()
    
    # Group by device_id to avoid sending duplicate IP configs
    configs = {}
    for s in sensors:
        if s.device_id not in configs:
            configs[s.device_id] = {
                "device_id": s.device_id,
                "tapo_ip": s.tapo_ip,
                "tapo_username": s.tapo_username,
                "tapo_password": s.tapo_password
            }
    return list(configs.values())


class HeartbeatRequest(BaseModel):
    devices: dict
    cycle_errors: dict
    timestamp: datetime


@router.post("/tapo/heartbeat", tags=["Edge"])
def tapo_heartbeat(
    req: HeartbeatRequest,
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_edge_api_key)
):
    """Receive heartbeat from the edge agent and update plug statuses in the DB."""
    for device_id, status_data in req.devices.items():
        sensor = db.query(Sensor).filter(
            Sensor.device_id == device_id,
            Sensor.type == "temperature",
            Sensor.active == True
        ).first()
        if sensor:
            sensor.tapo_status = status_data.get("status")
            sensor.tapo_error = status_data.get("error")
            
            last_success = status_data.get("last_success")
            if last_success:
                try:
                    # fromisoformat might need to handle 'Z'
                    clean_ts = last_success.replace("Z", "+00:00")
                    sensor.tapo_last_seen = datetime.fromisoformat(clean_ts).replace(tzinfo=None)
                except Exception:
                    sensor.tapo_last_seen = req.timestamp
            else:
                sensor.tapo_last_seen = req.timestamp
    db.commit()
    return {"message": "Heartbeat processed successfully"}


from pydantic import BaseModel
class PlugIngestRequest(BaseModel):
    apower: float
    voltage: float
    current: float
    today_energy: float
    month_energy: float


@router.post("/device/{device_id}/plug/ingest", tags=["Edge"])
def ingest_plug_telemetry(
    device_id: str,
    req: PlugIngestRequest,
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_edge_api_key)
):
    """Secure endpoint for the edge script to push Tapo telemetry."""
    from backend.models.plug_telemetry import PlugTelemetry
    
    log = PlugTelemetry(
        device_id=device_id,
        apower=req.apower,
        voltage=req.voltage,
        current=req.current,
        today_energy=req.today_energy,
        month_energy=req.month_energy,
        timestamp=datetime.utcnow()
    )
    db.add(log)
    db.commit()
    return {"message": "Tapo telemetry ingested successfully"}


@router.get("/export-csv", tags=["Sensors"])
def export_sensor_csv(
    sensor_id: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    db: Session = Depends(get_db),
    user: TokenUser = Depends(get_current_user)
):
    """
    Export aggregated daily metrics for a sensor within a date range as a CSV.
    """
    try:
        # Resolve sensor
        sensor = db.query(Sensor).filter(Sensor.id == sensor_id).first()
        if not sensor:
            raise HTTPException(status_code=404, detail="Sensor not found")

        # Query and aggregate
        query = db.query(
            cast(SensorReading.recorded_at, Date).label("day"),
            func.avg(SensorReading.value).label("avg_val"),
            func.min(SensorReading.value).label("min_val"),
            func.max(SensorReading.value).label("max_val"),
            func.count(SensorReading.value).label("cnt")
        ).filter(SensorReading.sensor_id == sensor.id)

        if date_from:
            query = query.filter(SensorReading.recorded_at >= date_from)
        if date_to:
            query = query.filter(SensorReading.recorded_at <= date_to + timedelta(days=1))

        results = query.group_by(cast(SensorReading.recorded_at, Date)).order_by("day").all()

        # Generate CSV
        csv_file = StringIO()
        writer = csv.writer(csv_file)
        
        # Header
        writer.writerow(["Date", "Sensor Name", "Sensor Type", "Average Value", "Min Value", "Max Value", "Readings Count"])
        
        for row in results:
            writer.writerow([
                row.day.strftime("%Y-%m-%d") if row.day else "",
                sensor.name,
                sensor.type,
                round(float(row.avg_val), 2) if row.avg_val is not None else "",
                round(float(row.min_val), 2) if row.min_val is not None else "",
                round(float(row.max_val), 2) if row.max_val is not None else "",
                row.cnt
            ])
            
        csv_file.seek(0)
        
        filename = f"sensor_{sensor.name.replace(' ', '_')}_report.csv"
        headers = {
            "Content-Disposition": f"attachment; filename={filename}"
        }
        return StreamingResponse(
            iter([csv_file.getvalue()]),
            media_type="text/csv",
            headers=headers
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to export CSV: {str(e)}")


@router.get("/device/{device_id}/compressor-analytics")
def get_compressor_analytics(device_id: str, db: Session = Depends(get_db)):
    """Fetch historical compressor statistics for graphs/histograms."""
    sensor = db.query(Sensor).filter(
        Sensor.device_id == device_id,
        Sensor.type == "temperature",
        Sensor.active == True
    ).first()
    if not sensor:
        raise HTTPException(status_code=404, detail="Temperature sensor not found")
        
    from backend.models.compressor_stats import CompressorStats
    cutoff_date = (datetime.utcnow() - timedelta(days=30)).date()
    
    stats_list = db.query(CompressorStats).filter(
        CompressorStats.sensor_id == sensor.id,
        CompressorStats.date >= cutoff_date
    ).order_by(CompressorStats.date.asc()).all()
    
    return [{
        "date": s.date.isoformat(),
        "cycle_count": s.cycle_count,
        "total_runtime_minutes": float(s.total_runtime_minutes) if s.total_runtime_minutes else 0.0,
        "avg_runtime_per_cycle_minutes": float(s.avg_runtime_per_cycle_minutes) if s.avg_runtime_per_cycle_minutes else 0.0,
        "daily_energy_kwh": float(s.daily_energy_kwh) if s.daily_energy_kwh else 0.0,
        "monthly_energy_kwh": float(s.monthly_energy_kwh) if s.monthly_energy_kwh else 0.0,
        "estimated_cost": float(s.estimated_cost) if s.estimated_cost else 0.0
    } for s in stats_list]


@router.get("/device/{device_id}/door-logs")
def get_device_door_logs(device_id: str, db: Session = Depends(get_db)):
    """Fetch the door opened/closed log events for a device."""
    sensor = db.query(Sensor).filter(
        Sensor.device_id == device_id,
        Sensor.type == "temperature",
        Sensor.active == True
    ).first()
    if not sensor:
        raise HTTPException(status_code=404, detail="Temperature sensor not found")
        
    from backend.models.door_event import DoorEvent
    events = db.query(DoorEvent).filter(
        DoorEvent.sensor_id == sensor.id
    ).order_by(DoorEvent.opened_at.desc()).all()
    
    return [{
        "id": str(e.id),
        "opened_at": e.opened_at.isoformat(),
        "closed_at": e.closed_at.isoformat() if e.closed_at else None,
        "duration_seconds": e.duration_seconds
    } for e in events]


@router.get("/device/{device_id}/ai-summary")
async def get_device_ai_summary(device_id: str, db: Session = Depends(get_db)):
    """Fetch Gemini AI maintenance summary & diagnostic recommendations for a device."""
    sensor = db.query(Sensor).filter(
        Sensor.device_id == device_id,
        Sensor.type == "temperature",
        Sensor.active == True
    ).first()
    if not sensor:
        raise HTTPException(status_code=404, detail="Temperature sensor not found")
        
    room = db.query(Room).filter(Room.id == sensor.room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
        
    cutoff_24h = datetime.utcnow() - timedelta(hours=24)
    sensors_list = db.query(Sensor).filter(Sensor.room_id == room.id, Sensor.active == True).all()
    
    from backend.services.insights import query_24h_room_metrics, fetch_7d_baselines, call_gemini_diagnose, fetch_7d_daily_history
    
    last_24h = query_24h_room_metrics(db, room, cutoff_24h, sensors_list)
    baselines = fetch_7d_baselines(db, room, sensors_list)
    history = fetch_7d_daily_history(db, room, sensors_list)
    
    telemetry_data = [{
        "room_name": room.name,
        "room_type": room.type,
        "last_24h": last_24h,
        "baseline_7d": baselines,
        "daily_history_7d": history
    }]
    
    insights = await call_gemini_diagnose(telemetry_data)
    diagnoses = insights.get("diagnoses", [])
    if diagnoses:
        return diagnoses[0]
        
    return {
        "room_name": room.name,
        "status": "healthy",
        "analysis": "No diagnostics findings returned by the AI engine.",
        "action_items": ["Verify sensor connections and threshold calibrations."]
    }

