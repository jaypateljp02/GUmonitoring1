"""Sensor routes with auto-alert logic."""
from typing import List
from datetime import datetime, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
import os

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

def verify_edge_api_key(x_api_key: str = Header(...)):
    expected_key = os.getenv("EDGE_API_KEY", os.getenv("JWT_SECRET", "default-secret"))
    if x_api_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return x_api_key


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
        FROM device_telemetry
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
            FROM {table_name}
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
    
    latest_query = text(f"SELECT timestamp FROM {table_name} WHERE device_id = :device_id ORDER BY timestamp DESC LIMIT 1")
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
        FROM plug_telemetry
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
            import asyncio
            telemetry = await asyncio.wait_for(get_tapo_telemetry_cached(
                sensor.tapo_ip, sensor.tapo_username, sensor.tapo_password, device_id
            ), timeout=1.5)
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
