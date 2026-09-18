"""Sensor routes with auto-alert logic."""
from typing import List, Optional
from datetime import datetime, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
import os
import logging

logger = logging.getLogger(__name__)

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
from backend.schemas import ReadingCreate, ReadingResponse, SensorThresholdUpdate, MessageResponse, DeviceTelemetryResponse, MockControlRequest, DeviceMetrics24hResponse, MonthlyAnalyticsResponse, DailyMetric, BatchContextResponse, DeviceTelemetryHistoryResponse, PlugMetrics24hResponse, MonthlyCostSummaryResponse, BillingRateUpdateRequest, MonthlyDeviceBreakdown, MonthlyCategoryRollup, DailyTrendPoint, MonthlySummaryAggregate
from backend.models.device_telemetry import DeviceTelemetry
from backend.models.room import Room
from backend.models.plug_telemetry import PlugTelemetry
from sqlalchemy import text
from fastapi.responses import StreamingResponse
import csv
from io import StringIO
from sqlalchemy import func, cast, Date
import calendar

router = APIRouter(prefix="/sensors", tags=["Sensors"])

_DISCOVERED_TAPO_PLUGS = {}  # In-memory cache: IP -> {"ip": "192.168.0.x", "model": "P110", "timestamp": datetime}


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

def calculate_offline_periods(db: Session, table_name: str, device_id: str, start_time: datetime, threshold_minutes: int = 12) -> List[dict]:
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
        res_list = []
        for log in logs:
            p_val = float(log.apower)
            if p_val > 1000.0:
                p_val = round(p_val / 100.0, 1)
            t_val = float(log.today_energy)
            if t_val > 10.0:
                t_val = round(t_val / 100.0, 3)
            m_val = float(log.month_energy)
            if m_val > 10.0:
                m_val = round(m_val / 100.0, 3)
            res_list.append({
                "id": str(log.id),
                "device_id": log.device_id,
                "timestamp": log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "apower": p_val,
                "voltage": float(log.voltage),
                "current": float(log.current),
                "today_energy": t_val,
                "month_energy": m_val
            })
        return res_list
        
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
    
    res_list = []
    for row in agg_results:
        p_val = round(row.apower, 1) if row.apower else 0.0
        if p_val > 1000.0:
            p_val = round(p_val / 100.0, 1)
        t_val = round(row.today_energy, 3) if row.today_energy else 0.0
        if t_val > 10.0:
            t_val = round(t_val / 100.0, 3)
        m_val = round(row.month_energy, 3) if row.month_energy else 0.0
        if m_val > 10.0:
            m_val = round(m_val / 100.0, 3)
        res_list.append({
            "device_id": device_id,
            "timestamp": row.bucket.strftime("%Y-%m-%d %H:%M:%S"),
            "apower": p_val,
            "voltage": round(row.voltage, 1) if row.voltage else 0.0,
            "current": round(row.current, 3) if row.current else 0.0,
            "today_energy": t_val,
            "month_energy": m_val
        })
    return res_list

@router.get("/device/{device_id}/telemetry", response_model=DeviceTelemetryHistoryResponse)
def get_device_telemetry(
    device_id: str, days: str = "1", interval_minutes: int = 1, start_date: Optional[str] = None, end_date: Optional[str] = None, db: Session = Depends(get_db)
):
    if start_date and end_date:
        try:
            start_local = datetime.fromisoformat(start_date) if "T" in start_date else datetime.strptime(start_date, "%Y-%m-%d")
            end_local = datetime.fromisoformat(end_date) if "T" in end_date else datetime.strptime(end_date, "%Y-%m-%d")
            start_local = start_local.replace(hour=0, minute=0, second=0, microsecond=0)
            end_local = end_local.replace(hour=23, minute=59, second=59, microsecond=999999)
            cutoff = start_local - timedelta(hours=5, minutes=30)
            end_time = end_local - timedelta(hours=5, minutes=30)
        except Exception as err:
            raise HTTPException(status_code=400, detail=f"Invalid date format. Use YYYY-MM-DD. Error: {err}")
    else:
        try:
            days_count = int(days)
        except (ValueError, TypeError):
            days_count = 1
        cutoff = datetime.utcnow() - timedelta(days=days_count)
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
    device_id: str,
    days: str = "1",
    interval_minutes: int = 1,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    include_plug: Optional[str] = "true",
    channels: Optional[str] = "all",
    db: Session = Depends(get_db)
):
    """
    Export combined temperature, humidity, battery, AND smart plug power telemetry CSV.
    Supports filtering by channels: 'all', 'fridge_power', 'freezer_power', 'sensor_only'.
    """
    from backend.models.plug_telemetry import PlugTelemetry
    
    if start_date and end_date:
        try:
            start_local = datetime.fromisoformat(start_date) if "T" in start_date else datetime.strptime(start_date, "%Y-%m-%d")
            end_local = datetime.fromisoformat(end_date) if "T" in end_date else datetime.strptime(end_date, "%Y-%m-%d")
            start_local = start_local.replace(hour=0, minute=0, second=0, microsecond=0)
            end_local = end_local.replace(hour=23, minute=59, second=59, microsecond=999999)
            cutoff = start_local - timedelta(hours=5, minutes=30)
            end_time = end_local - timedelta(hours=5, minutes=30)
            filename = f"telemetry_{device_id}_{channels}_{start_date}_to_{end_date}.csv"
        except Exception as err:
            raise HTTPException(status_code=400, detail=f"Invalid date format. Use YYYY-MM-DD. Error: {err}")
    else:
        try:
            days_count = int(days)
        except (ValueError, TypeError):
            days_count = 1
        cutoff = datetime.utcnow() - timedelta(days=days_count)
        end_time = datetime.utcnow()
        filename = f"telemetry_{device_id}_{channels}_{days}d.csv"
        
    sensor = db.query(Sensor).filter(Sensor.device_id == device_id).first()
    room_sensors = []
    if sensor and sensor.room_id:
        room_sensors = db.query(Sensor).filter(Sensor.room_id == sensor.room_id).all()
        
    temp_device_ids = list(set([device_id] + [s.device_id for s in room_sensors if s.type == "temperature"]))
    plug_device_ids = list(set([device_id] + [s.device_id for s in room_sensors if s.type == "plug"]))
    
    target_temp_id = temp_device_ids[0] if temp_device_ids else device_id
    aggregated_logs = aggregate_telemetry(db, target_temp_id, cutoff, end_time, interval_minutes)
    aggregated_logs = list(reversed(aggregated_logs))
    
    want_plug = (include_plug and include_plug.lower() == "true") and (channels != "sensor_only")
    plug_logs = []
    plug_logs_map = {}
    
    if want_plug:
        plug_logs = db.query(PlugTelemetry).filter(
            PlugTelemetry.device_id.in_(plug_device_ids),
            PlugTelemetry.timestamp >= cutoff,
            PlugTelemetry.timestamp <= end_time
        ).order_by(PlugTelemetry.timestamp.asc()).all()
        
        for pl in plug_logs:
            t_key = pl.timestamp.strftime('%Y-%m-%d %H:%M')
            if t_key not in plug_logs_map:
                plug_logs_map[t_key] = pl

    ist_offset = timedelta(hours=5, minutes=30)
    output = StringIO()
    writer = csv.writer(output)
    
    headers = ["Device ID", "Timestamp (UTC)", "Timestamp (IST)", "Temperature (C)", "Humidity (%)", "Battery (%)"]
    if want_plug:
        headers.extend(["Active Power (W)", "Voltage (V)", "Current (A)", "Today Energy (kWh)"])
        
    writer.writerow(headers)
    
    if aggregated_logs:
        for log in aggregated_logs:
            utc_str = log.timestamp.strftime('%Y-%m-%d %H:%M:%S')
            ist_str = (log.timestamp + ist_offset).strftime('%Y-%m-%d %H:%M:%S')
            t_key = log.timestamp.strftime('%Y-%m-%d %H:%M')
            
            row = [
                log.device_id,
                utc_str,
                ist_str,
                str(log.temperature) if log.temperature is not None else "",
                str(log.humidity) if log.humidity is not None else "",
                str(log.battery_level) if log.battery_level is not None else ""
            ]
            
            if want_plug:
                pl_data = plug_logs_map.get(t_key)
                if not pl_data and plug_logs:
                    # Nearest minute matching fallback within 3 mins
                    for pl in plug_logs:
                        if abs((pl.timestamp - log.timestamp).total_seconds()) <= 180:
                            pl_data = pl
                            break
                            
                if pl_data:
                    today_wh = float(pl_data.today_energy or 0.0)
                    today_kwh = round(today_wh / 1000.0 if today_wh > 500.0 else today_wh, 3)
                    row.extend([
                        str(round(float(pl_data.apower or 0.0), 1)),
                        str(round(float(pl_data.voltage or 0.0), 1)),
                        str(round(float(pl_data.current or 0.0), 3)),
                        str(today_kwh)
                    ])
                else:
                    row.extend(["", "", "", ""])
                    
            writer.writerow(row)
    elif plug_logs and want_plug:
        for pl in plug_logs:
            utc_str = pl.timestamp.strftime('%Y-%m-%d %H:%M:%S')
            ist_str = (pl.timestamp + ist_offset).strftime('%Y-%m-%d %H:%M:%S')
            today_wh = float(pl.today_energy or 0.0)
            today_kwh = round(today_wh / 1000.0 if today_wh > 500.0 else today_wh, 3)
            writer.writerow([
                pl.device_id,
                utc_str,
                ist_str,
                "", "", "",
                str(round(float(pl.apower or 0.0), 1)),
                str(round(float(pl.voltage or 0.0), 1)),
                str(round(float(pl.current or 0.0), 3)),
                str(today_kwh)
            ])
            
    output.seek(0)
    response = StreamingResponse(iter([output.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
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
    """Public endpoint: update thresholds, webhooks, and Tapo/eWeLink settings for a device's sensors."""
    sensors = db.query(Sensor).filter(Sensor.device_id == device_id, Sensor.active == True).all()
    if not sensors:
        raise HTTPException(status_code=404, detail="Device not found")

    # Generic & specific keys
    billing_rate = req.get("tapo_billing_rate") if "tapo_billing_rate" in req else (req.get("billing_rate") if "billing_rate" in req else (req.get("temp_tapo_billing_rate") if "temp_tapo_billing_rate" in req else req.get("hum_tapo_billing_rate")))
    running_threshold = req.get("tapo_running_threshold") if "tapo_running_threshold" in req else (req.get("running_threshold") if "running_threshold" in req else (req.get("temp_tapo_running_threshold") if "temp_tapo_running_threshold" in req else req.get("hum_tapo_running_threshold")))
    tapo_ip = req.get("tapo_ip") if "tapo_ip" in req else (req.get("temp_tapo_ip") if "temp_tapo_ip" in req else req.get("hum_tapo_ip"))
    tapo_user = req.get("tapo_username") if "tapo_username" in req else (req.get("temp_tapo_username") if "temp_tapo_username" in req else req.get("hum_tapo_username"))
    tapo_pass = req.get("tapo_password") if "tapo_password" in req else (req.get("temp_tapo_password") if "temp_tapo_password" in req else req.get("hum_tapo_password"))

    for s in sensors:
        if billing_rate is not None:
            s.tapo_billing_rate = billing_rate
        if running_threshold is not None:
            s.tapo_running_threshold = running_threshold
        if tapo_ip is not None:
            s.tapo_ip = tapo_ip
        if tapo_user is not None:
            s.tapo_username = tapo_user
        if tapo_pass is not None:
            s.tapo_password = tapo_pass

        if s.type == "temperature":
            if "temp_min" in req:
                s.min_threshold = req["temp_min"]
            if "temp_max" in req:
                s.max_threshold = req["temp_max"]
            if "temp_alert_webhook_url" in req:
                s.alert_webhook_url = req["temp_alert_webhook_url"]
            if "temp_recovery_webhook_url" in req:
                s.recovery_webhook_url = req["temp_recovery_webhook_url"]
        elif s.type == "humidity":
            if "hum_min" in req:
                s.min_threshold = req["hum_min"]
            if "hum_max" in req:
                s.max_threshold = req["hum_max"]
            if "hum_alert_webhook_url" in req:
                s.alert_webhook_url = req["hum_alert_webhook_url"]
            if "hum_recovery_webhook_url" in req:
                s.recovery_webhook_url = req["hum_recovery_webhook_url"]
        elif s.type == "plug":
            pass

    db.commit()
    return {"message": "Thresholds, webhooks, and plug configurations updated"}

@router.put("/device/{device_id}/room")
def update_device_room(device_id: str, req: dict, db: Session = Depends(get_db)):
    """Assign all sensors of a device to a room (or unmerge/clear room if None/'unmerge')."""
    sensors = db.query(Sensor).filter(Sensor.device_id == device_id, Sensor.active == True).all()
    if not sensors:
        raise HTTPException(status_code=404, detail="Device not found")
    room_id = req.get("room_id")
    if room_id == "unmerge" or room_id == "" or room_id == "null":
        room_id = None
    for s in sensors:
        s.room_id = room_id
    db.commit()
    return {"message": "Device room updated successfully"}

@router.get("/device/{device_id}/plug")
async def get_device_plug_status(device_id: str, db: Session = Depends(get_db)):
    """Dynamic endpoint to fetch plug telemetry (voltage, current, power) from Tapo or eWeLink.
    Falls back to last known DB record when direct LAN connection is unavailable.
    """
    sensor = db.query(Sensor).filter(
        Sensor.device_id == device_id,
        Sensor.active == True
    ).first()

    target_plug_id = device_id
    target_sensor = sensor
    if sensor and sensor.type != "plug" and sensor.room_id:
        plug_sensor = db.query(Sensor).filter(
            Sensor.room_id == sensor.room_id,
            Sensor.type == "plug",
            Sensor.active == True
        ).first()
        if plug_sensor:
            target_plug_id = plug_sensor.device_id
            target_sensor = plug_sensor

    rate = 10.0
    if target_sensor and target_sensor.tapo_billing_rate is not None:
        rate = float(target_sensor.tapo_billing_rate)

    # 1. Fast path: return PlugTelemetry database records ingested by edge agent or eWeLink worker
    from backend.models.plug_telemetry import PlugTelemetry
    last_log = db.query(PlugTelemetry).filter(
        PlugTelemetry.device_id == target_plug_id
    ).order_by(PlugTelemetry.timestamp.desc()).first()

    now = datetime.utcnow()
    if last_log:
        age_seconds = (now - last_log.timestamp).total_seconds()
        is_stale = age_seconds > 600.0  # Mark last_known if older than 10 mins
        raw_t_energy = float(last_log.today_energy or 0.0)
        raw_m_energy = float(last_log.month_energy or 0.0)
        today_kwh = (raw_t_energy / 1000.0) if raw_t_energy > 500.0 else raw_t_energy
        month_kwh = (raw_m_energy / 1000.0) if raw_m_energy > 5000.0 else raw_m_energy
        p_val = float(last_log.apower or 0.0) if not is_stale else 0.0
        v_val = float(last_log.voltage or 230.0) if float(last_log.voltage or 0.0) > 0 else 230.0
        c_val = float(last_log.current or 0.0) if not is_stale else 0.0
        sw_state = "on" if p_val > 0.5 else "off"

        is_ewelink = (target_sensor and target_sensor.name and "ewelink" in target_sensor.name.lower()) or str(target_plug_id).startswith("100") or (target_sensor and target_sensor.type == "plug")
        plug_type = "ewelink" if is_ewelink else "tapo"

        return {
            "state": sw_state,
            "voltage": round(v_val, 1),
            "current": round(c_val, 3),
            "apower": round(p_val, 1),
            "today_energy": raw_t_energy,
            "month_energy": raw_m_energy,
            "today_kwh": round(today_kwh, 3),
            "month_kwh": round(month_kwh, 3),
            "today_bill": round(today_kwh * rate, 2),
            "month_bill": round(month_kwh * rate, 2),
            "billing_rate": rate,
            "supported": True,
            "type": plug_type,
            "last_known": is_stale,
            "last_known_at": last_log.timestamp.strftime("%Y-%m-%d %H:%M:%S") if is_stale else None
        }

    # 2. Slow fallback: Try direct LAN query if on local network
    if target_sensor and target_sensor.tapo_ip and target_sensor.tapo_username and target_sensor.tapo_password:
        try:
            from backend.services.tapo import get_tapo_telemetry_cached
            import asyncio
            telemetry = await asyncio.wait_for(get_tapo_telemetry_cached(
                target_sensor.tapo_ip, target_sensor.tapo_username, target_sensor.tapo_password, target_plug_id
            ), timeout=0.4)
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
            log.error(f"Direct Tapo connection failed for {target_plug_id} ({target_sensor.tapo_ip}): {e}")

    # Try live eWeLink cloud status for eWeLink power devices (POWR320D)
    import os
    import asyncio
    from dotenv import load_dotenv
    load_dotenv()
    if not os.getenv("EWELINK_EMAIL"):
        load_dotenv("backend/.env")

    email = os.getenv("EWELINK_EMAIL")
    password = os.getenv("EWELINK_PASSWORD")
    region = os.getenv("EWELINK_REGION", "as")

    if email and password:
        try:
            from backend.services.ewelink import EwelinkClient
            ew_client = EwelinkClient(email=email, password=password, region=region)
            login_ok = await asyncio.wait_for(ew_client.login(), timeout=4.0)
            if login_ok:
                status = await asyncio.wait_for(ew_client.get_power_device_status(target_plug_id), timeout=4.0)
                if status:
                    today_kwh = status.get("today_energy", 0.0)
                    month_kwh = status.get("month_energy", 0.0)
                    sw_state = status.get("switch", "off").lower()
                    p_val = status.get("power", 0.0) if sw_state == "on" else 0.0
                    c_val = status.get("current", 0.0) if sw_state == "on" else 0.0
                    return {
                        "state": sw_state,
                        "voltage": status.get("voltage", 0.0),
                        "current": c_val,
                        "apower": p_val,
                        "today_energy": today_kwh,
                        "month_energy": month_kwh,
                        "today_kwh": round(today_kwh, 3),
                        "month_kwh": round(month_kwh, 3),
                        "today_bill": round(today_kwh * rate, 2),
                        "month_bill": round(month_kwh * rate, 2),
                        "billing_rate": rate,
                        "supported": True,
                        "type": "plug",
                        "last_known": False
                    }
        except Exception as e:
            logger.error(f"Live eWeLink status check failed for {target_plug_id}: {e}")

    # Fallback / DB log path: serve the most recent PlugTelemetry record stored by worker
    from backend.models.plug_telemetry import PlugTelemetry
    last_log = db.query(PlugTelemetry).filter(
        PlugTelemetry.device_id == target_plug_id
    ).order_by(PlugTelemetry.timestamp.desc()).first()

    if last_log:
        raw_t_energy = float(last_log.today_energy)
        raw_m_energy = float(last_log.month_energy)
        
        today_kwh = raw_t_energy if raw_t_energy > 0.001 else 0.0
        if today_kwh == 0.0:
            today_start = datetime.combine(datetime.utcnow().date(), datetime.min.time())
            today_recs = db.query(PlugTelemetry).filter(
                PlugTelemetry.device_id == target_plug_id,
                PlugTelemetry.timestamp >= today_start
            ).order_by(PlugTelemetry.timestamp.asc()).all()
            if today_recs:
                avg_power = sum(float(r.apower) for r in today_recs) / float(len(today_recs))
                first_ts = today_recs[0].timestamp
                hrs = max(0.083, (datetime.utcnow() - first_ts).total_seconds() / 3600.0)
                today_kwh = (avg_power * hrs) / 1000.0

        month_kwh = raw_m_energy if raw_m_energy > 0.001 else 0.0
        if month_kwh == 0.0:
            month_start = datetime.combine(datetime.utcnow().date().replace(day=1), datetime.min.time())
            month_recs = db.query(PlugTelemetry).filter(
                PlugTelemetry.device_id == target_plug_id,
                PlugTelemetry.timestamp >= month_start
            ).order_by(PlugTelemetry.timestamp.asc()).all()
            if month_recs:
                avg_power_m = sum(float(r.apower) for r in month_recs) / float(len(month_recs))
                first_ts_m = month_recs[0].timestamp
                hrs_m = max(0.083, (datetime.utcnow() - first_ts_m).total_seconds() / 3600.0)
                month_kwh = (avg_power_m * hrs_m) / 1000.0

        # Check if telemetry is older than 3 minutes (180 seconds)
        is_stale = (datetime.utcnow() - last_log.timestamp).total_seconds() > 180.0
        if is_stale:
            logger.warning(f"Plug telemetry for {target_plug_id} is stale (last seen {last_log.timestamp}). Marking offline.")
            return {
                "state": "offline",
                "voltage": 0.0,
                "current": 0.0,
                "apower": 0.0,
                "today_energy": raw_t_energy,
                "month_energy": raw_m_energy,
                "today_kwh": round(today_kwh, 3),
                "month_kwh": round(month_kwh, 3),
                "today_bill": round(today_kwh * rate, 2),
                "month_bill": round(month_kwh * rate, 2),
                "billing_rate": rate,
                "supported": True,
                "type": "plug",
                "error": f"Plug is disconnected (offline since {last_log.timestamp.strftime('%Y-%m-%d %H:%M UTC')})"
            }
        
        return {
            "state": "on" if float(last_log.apower) > 0.5 else "off",
            "voltage": float(last_log.voltage),
            "current": float(last_log.current),
            "apower": float(last_log.apower),
            "today_energy": raw_t_energy,
            "month_energy": raw_m_energy,
            "today_kwh": round(today_kwh, 3),
            "month_kwh": round(month_kwh, 3),
            "today_bill": round(today_kwh * rate, 2),
            "month_bill": round(month_kwh * rate, 2),
            "billing_rate": rate,
            "supported": True,
            "type": "plug",
            "last_known": False,
            "last_known_at": last_log.timestamp.strftime("%Y-%m-%d %H:%M UTC")
        }

    return {
        "state": "off",
        "voltage": 0.0,
        "current": 0.0,
        "apower": 0.0,
        "supported": False
    }

@router.post("/device/{device_id}/plug/toggle")
async def toggle_device_plug(device_id: str, req: dict, db: Session = Depends(get_db)):
    """
    Toggle plug power state ('on' or 'off') for Tapo or eWeLink power devices.
    """
    import decimal
    import os
    from dotenv import load_dotenv
    load_dotenv()

    target_state = req.get("state", "on").lower()
    sensor = db.query(Sensor).filter(
        Sensor.device_id == device_id,
        Sensor.active == True
    ).first()

    if not sensor:
        raise HTTPException(status_code=404, detail="Device not found")

    target_plug_id = device_id
    target_sensor = sensor

    # If requested device is not a plug, check if room has a linked plug sensor
    if sensor.type != "plug" and sensor.room_id:
        plug_sensor = db.query(Sensor).filter(
            Sensor.room_id == sensor.room_id,
            Sensor.type == "plug",
            Sensor.active == True
        ).first()
        if plug_sensor:
            target_plug_id = plug_sensor.device_id
            target_sensor = plug_sensor

    # Case 1: Tapo Plug (has tapo_ip & tapo_username/password)
    if target_sensor.tapo_ip and target_sensor.tapo_username and target_sensor.tapo_password:
        try:
            from backend.services.tapo import toggle_tapo_plug
            res = await toggle_tapo_plug(target_sensor.tapo_ip, target_sensor.tapo_username, target_sensor.tapo_password, target_state)
            return res
        except Exception as e:
            logger.error(f"Failed to toggle Tapo plug {target_plug_id} ({target_sensor.tapo_ip}): {e}")
            raise HTTPException(status_code=500, detail=f"Failed to toggle Tapo plug: {e}")

    # Case 2: eWeLink Cloud Plug (POWR320D or eWeLink brand plug)
    if target_sensor.type == "plug" or target_plug_id == "10029128ab" or getattr(target_sensor, "brand", None) == "ewelink":
        email = os.getenv("EWELINK_EMAIL")
        password = os.getenv("EWELINK_PASSWORD")
        region = os.getenv("EWELINK_REGION", "as")

        if not email or not password:
            raise HTTPException(status_code=401, detail="eWeLink cloud credentials missing in server config.")

        try:
            from backend.services.ewelink import EwelinkClient
            ew_client = EwelinkClient(email=email, password=password, region=region)
            ok = await ew_client.login()
            if ok:
                success = await ew_client.set_device_switch(target_plug_id, target_state)
                if success:
                    from backend.models.plug_telemetry import PlugTelemetry
                    last_rec = db.query(PlugTelemetry).filter(PlugTelemetry.device_id == target_plug_id).order_by(PlugTelemetry.timestamp.desc()).first()
                    t_energy = last_rec.today_energy if last_rec else decimal.Decimal("150.0")
                    m_energy = last_rec.month_energy if last_rec else decimal.Decimal("150.0")
                    p_val = decimal.Decimal("0.0") if target_state == "off" else decimal.Decimal("126.9")
                    c_val = decimal.Decimal("0.0") if target_state == "off" else decimal.Decimal("1.04")
                    v_val = decimal.Decimal("239.0") if target_state == "off" else decimal.Decimal("240.0")

                    new_log = PlugTelemetry(
                        device_id=target_plug_id,
                        timestamp=datetime.utcnow(),
                        apower=p_val,
                        voltage=v_val,
                        current=c_val,
                        today_energy=t_energy,
                        month_energy=m_energy
                    )
                    db.add(new_log)
                    db.commit()
                    return {"message": f"Successfully toggled eWeLink plug {target_plug_id} to {target_state}", "state": target_state}
                else:
                    raise HTTPException(status_code=500, detail=f"Failed to toggle eWeLink plug {target_plug_id}. Cloud rejected command.")
            else:
                raise HTTPException(status_code=401, detail="Failed to authenticate with eWeLink cloud.")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error toggling eWeLink plug {target_plug_id}: {e}")
            raise HTTPException(status_code=500, detail=f"eWeLink toggle error: {e}")

    # Case 3: No smart plug linked to this room/device
    raise HTTPException(status_code=400, detail="No smart plug is configured or linked to this room.")

@router.get("/device/{device_id}/plug/metrics/24h", response_model=PlugMetrics24hResponse)
def get_plug_24h_metrics(device_id: str, db: Session = Depends(get_db)):
    """Get 24-hour min/max/avg metrics for plug telemetry, weekly baselines, and status diagnosis."""
    from backend.models.plug_telemetry import PlugTelemetry
    from backend.models.device_telemetry import DeviceTelemetry
    from collections import defaultdict
    
    # 1. Map target plug sensor ID if current device is a temperature sensor
    target_plug_id = device_id
    curr_sensor = db.query(Sensor).filter(Sensor.device_id == device_id, Sensor.active == True).first()
    if curr_sensor and curr_sensor.type != "plug" and curr_sensor.room_id:
        plug_sensor = db.query(Sensor).filter(
            Sensor.room_id == curr_sensor.room_id,
            Sensor.type == "plug",
            Sensor.active == True
        ).first()
        if plug_sensor:
            target_plug_id = plug_sensor.device_id
    
    threshold = 80.0
    if curr_sensor and curr_sensor.tapo_running_threshold is not None:
        threshold = float(curr_sensor.tapo_running_threshold)

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
        PlugTelemetry.device_id == target_plug_id,
        PlugTelemetry.timestamp >= cutoff_24h
    ).first()

    raw_energy = float(stats.energy_max) if (stats and stats.energy_max is not None) else 0.0
    energy_kwh = round(raw_energy / 100.0, 3) if raw_energy > 10.0 else round(raw_energy, 3)

    # 3. Calculate last 24h Use Time (runtime) and On/Off Cycles
    logs_24h = db.query(PlugTelemetry).filter(
        PlugTelemetry.device_id == target_plug_id,
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
        PlugTelemetry.device_id == target_plug_id,
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
        daily_energies.append(max_energy / 100.0 if max_energy > 10.0 else max_energy)

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
    if curr_sensor and curr_sensor.max_threshold is not None and current_temp is not None:
        if current_temp > float(curr_sensor.max_threshold):
            temp_high = True

    if temp_high:
        if compressor_state == "running":
            diagnosis = "cooling_fail"
            observation_msg = f"Observation: Temp is high ({current_temp:.1f}°C > {float(curr_sensor.max_threshold):.1f}°C) while the compressor is actively running (average: {round(stats.p_avg, 0) if (stats and stats.p_avg is not None) else 0.0} W)."
            abnormal_flags.append("cooling_failure")
        elif compressor_state == "idle":
            diagnosis = "power_issue"
            observation_msg = f"Observation: Temp is high ({current_temp:.1f}°C > {float(curr_sensor.max_threshold):.1f}°C) but compressor has been idle/off (average: {round(stats.p_avg, 0) if (stats and stats.p_avg is not None) else 0.0} W)."
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
    device_id: str, days: str = "1", interval_minutes: int = 1, start_date: Optional[str] = None, end_date: Optional[str] = None, db: Session = Depends(get_db)
):
    """Fetch plug telemetry history for charts."""
    from backend.models.plug_telemetry import PlugTelemetry
    if start_date and end_date:
        try:
            start_local = datetime.fromisoformat(start_date) if "T" in start_date else datetime.strptime(start_date, "%Y-%m-%d")
            end_local = datetime.fromisoformat(end_date) if "T" in end_date else datetime.strptime(end_date, "%Y-%m-%d")
            start_local = start_local.replace(hour=0, minute=0, second=0, microsecond=0)
            end_local = end_local.replace(hour=23, minute=59, second=59, microsecond=999999)
            cutoff = start_local - timedelta(hours=5, minutes=30)
            end_time = end_local - timedelta(hours=5, minutes=30)
        except Exception as err:
            raise HTTPException(status_code=400, detail=f"Invalid date format. Use YYYY-MM-DD. Error: {err}")
    else:
        try:
            days_count = int(days)
        except (ValueError, TypeError):
            days_count = 1
        cutoff = datetime.utcnow() - timedelta(days=days_count)
        end_time = datetime.utcnow()
        
    logs = db.query(PlugTelemetry).filter(
        PlugTelemetry.device_id == device_id,
        PlugTelemetry.timestamp >= cutoff,
        PlugTelemetry.timestamp <= end_time
    ).order_by(PlugTelemetry.timestamp.asc()).all()  # Sort ascending for charts
    
    # Calculate offline periods using raw logs
    offline_periods = calculate_offline_periods(db, "plug_telemetry", device_id, cutoff)
    
    # Aggregate plug telemetry logs
    aggregated_logs = aggregate_plug_telemetry(db, device_id, cutoff, end_time, interval_minutes)
    
    return {
        "history": aggregated_logs,
        "offline_periods": offline_periods
    }

@router.get("/device/{device_id}/plug/export")
def export_plug_telemetry(
    device_id: str, days: str = "1", interval_minutes: int = 1, start_date: Optional[str] = None, end_date: Optional[str] = None, db: Session = Depends(get_db)
):
    """Export plug telemetry logs as a CSV file with formatted energy (kWh) and power readings."""
    if start_date and end_date:
        try:
            start_local = datetime.fromisoformat(start_date) if "T" in start_date else datetime.strptime(start_date, "%Y-%m-%d")
            end_local = datetime.fromisoformat(end_date) if "T" in end_date else datetime.strptime(end_date, "%Y-%m-%d")
            start_local = start_local.replace(hour=0, minute=0, second=0, microsecond=0)
            end_local = end_local.replace(hour=23, minute=59, second=59, microsecond=999999)
            cutoff = start_local - timedelta(hours=5, minutes=30)
            end_time = end_local - timedelta(hours=5, minutes=30)
            filename = f"plug_telemetry_{device_id}_{start_date}_to_{end_date}.csv"
        except Exception as err:
            raise HTTPException(status_code=400, detail=f"Invalid date format. Use YYYY-MM-DD. Error: {err}")
    else:
        try:
            days_count = int(days)
        except (ValueError, TypeError):
            days_count = 1
        cutoff = datetime.utcnow() - timedelta(days=days_count)
        end_time = datetime.utcnow()
        filename = f"plug_telemetry_{device_id}_{days}d.csv"
        
    aggregated_logs = aggregate_plug_telemetry(db, device_id, cutoff, end_time, interval_minutes)
    
    # Sort ascending (oldest first) for readability
    aggregated_logs = list(reversed(aggregated_logs))
    ist_offset = timedelta(hours=5, minutes=30)
    
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Device ID", 
        "Timestamp (UTC)", 
        "Timestamp (IST)", 
        "Active Power (W)", 
        "Voltage (V)", 
        "Current (A)", 
        "Today Energy (kWh)", 
        "Month Energy (kWh)"
    ])
    
    for log in aggregated_logs:
        utc_str = log["timestamp"]
        try:
            utc_dt = datetime.strptime(utc_str, '%Y-%m-%d %H:%M:%S')
            ist_str = (utc_dt + ist_offset).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            ist_str = utc_str
            
        today_wh = float(log.get("today_energy") or 0.0)
        month_wh = float(log.get("month_energy") or 0.0)
        
        # Convert Wh to kWh if value > 500
        today_kwh = round(today_wh / 1000.0 if today_wh > 500.0 else today_wh, 3)
        month_kwh = round(month_wh / 1000.0 if month_wh > 500.0 else month_wh, 3)

        writer.writerow([
            log.get("device_id", device_id),
            utc_str,
            ist_str,
            str(round(float(log.get("apower") or 0.0), 1)),
            str(round(float(log.get("voltage") or 0.0), 1)),
            str(round(float(log.get("current") or 0.0), 3)),
            str(today_kwh),
            str(month_kwh)
        ])
        
    output.seek(0)
    response = StreamingResponse(iter([output.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response


# ==============================================================================
# ==============================================================================
# SONOFF MONTHLY POWER & ELECTRICITY COST ANALYTICS
# ==============================================================================

async def get_live_ewelink_power_telemetry() -> dict:
    """Query live Sonoff telemetry directly from eWeLink cloud for current month."""
    import os
    import asyncio
    from dotenv import load_dotenv
    load_dotenv()
    if not os.getenv("EWELINK_EMAIL"):
        load_dotenv("backend/.env")

    email = os.getenv("EWELINK_EMAIL")
    password = os.getenv("EWELINK_PASSWORD")
    region = os.getenv("EWELINK_REGION", "as")
    if not email or not password:
        return {}

    try:
        from backend.services.ewelink import EwelinkClient
        client = EwelinkClient(email=email, password=password, region=region)
        ok = await asyncio.wait_for(client.login(), timeout=5.0)
        if not ok:
            return {}
        devices = await asyncio.wait_for(client.get_all_devices(), timeout=6.0)
        live_map = {}
        for d in devices:
            item = d.get("itemData", {})
            params = item.get("params", {})
            if EwelinkClient.is_power_device(params):
                dev_id = item.get("deviceid")
                m_raw = params.get("monthKwh")
                d_raw = params.get("dayKwh") or params.get("todayKwh")
                p_raw = params.get("power")
                v_raw = params.get("voltage")
                c_raw = params.get("current")
                sw_state = "off"
                if "switches" in params and isinstance(params["switches"], list) and len(params["switches"]) > 0:
                    sw_state = str(params["switches"][0].get("switch", "off")).lower()
                elif "switch" in params and params["switch"] is not None:
                    sw_state = str(params["switch"]).lower()

                p_val = round(float(p_raw) / 100.0, 2) if p_raw is not None else 0.0
                v_val = round(float(v_raw) / 100.0, 1) if v_raw is not None else 230.0
                c_val = round(float(c_raw) / 100.0, 2) if c_raw is not None else 0.0
                m_kwh = round(float(m_raw) / 100.0, 3) if m_raw is not None else 0.0
                d_kwh = round(float(d_raw) / 100.0, 3) if d_raw is not None else 0.0

                live_map[dev_id] = {
                    "month_kwh": m_kwh,
                    "today_kwh": d_kwh,
                    "power_w": p_val,
                    "voltage_v": v_val,
                    "current_a": c_val,
                    "state": sw_state,
                    "online": item.get("online", True)
                }
        return live_map
    except Exception as e:
        logger.error(f"Error fetching live eWeLink power map: {e}")
        return {}


@router.get("/plugs/monthly-summary", response_model=MonthlyCostSummaryResponse)
async def get_plugs_monthly_summary(month: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Get full factory power and electricity cost summary for Sonoff devices:
    - Combined factory total cost (INR) and energy (kWh)
    - Projected month-end utility forecast
    - Month-over-Month comparison
    - Category / department roll-up (Freezers, Fridges, Fermentation)
    - Daily cumulative energy and cost curve
    - Ranked individual device ledger with live status and tariffs
    - Smart anomaly and power hog insights
    """
    # 1. Discover available months with recorded telemetry
    months_query = db.execute(text("""
        SELECT DISTINCT to_char(timestamp, 'YYYY-MM') as month_str
        FROM monitoring.plug_telemetry
        ORDER BY month_str DESC
    """)).fetchall()
    available_months = [r[0] for r in months_query if r[0]]
    current_month_str = datetime.utcnow().strftime("%Y-%m")
    if current_month_str not in available_months:
        available_months.insert(0, current_month_str)

    # Ensure all past 12 calendar months are selectable in the month dropdown
    now_dt = datetime.utcnow()
    for i in range(12):
        m_idx = now_dt.month - i
        y_idx = now_dt.year
        while m_idx <= 0:
            m_idx += 12
            y_idx -= 1
        m_cand = f"{y_idx:04d}-{m_idx:02d}"
        if m_cand not in available_months:
            available_months.append(m_cand)
    available_months.sort(reverse=True)

    # 2. Determine target month and date boundaries (default to current month!)
    target_month = month.strip() if (month and month.strip()) else current_month_str
    if target_month not in available_months and len(target_month) == 7:
        available_months.append(target_month)
        available_months.sort(reverse=True)

    try:
        year, month_int = map(int, target_month.split("-"))
    except Exception:
        target_month = current_month_str
        year, month_int = map(int, target_month.split("-"))

    days_in_month = calendar.monthrange(year, month_int)[1]
    start_dt = datetime(year, month_int, 1, 0, 0, 0)
    end_dt = datetime(year, month_int, days_in_month, 23, 59, 59, 999999)
    month_name = datetime(year, month_int, 1).strftime("%B %Y")
    is_current_month = (target_month == current_month_str)
    days_elapsed = min(days_in_month, max(1, datetime.utcnow().day)) if is_current_month else days_in_month

    # 3. Calculate previous month boundaries for Month-over-Month delta
    if month_int == 1:
        prev_year, prev_month_int = year - 1, 12
    else:
        prev_year, prev_month_int = year, month_int - 1
    prev_days = calendar.monthrange(prev_year, prev_month_int)[1]
    prev_start_dt = datetime(prev_year, prev_month_int, 1, 0, 0, 0)
    prev_end_dt = datetime(prev_year, prev_month_int, prev_days, 23, 59, 59, 999999)

    # 4. Fetch live Sonoff eWeLink cloud telemetry if viewing current active month
    live_ewelink_map = {}
    if is_current_month:
        try:
            live_ewelink_map = await get_live_ewelink_power_telemetry()
        except Exception as e:
            logger.error(f"Live eWeLink telemetry fetch failed: {e}")

    # 5. Fetch all active plug sensors and linked rooms
    plug_sensors = db.query(Sensor).filter(
        Sensor.type == "plug",
        Sensor.active == True
    ).all()

    devices_list = []
    total_factory_energy = 0.0
    total_factory_cost = 0.0
    prev_total_cost = 0.0
    combined_live_power_w = 0.0
    active_device_count = 0
    now_utc = datetime.utcnow()

    rooms = {str(r.id): r for r in db.query(Room).all()}

    for s in plug_sensors:
        dev_id = s.device_id
        rate = float(s.tapo_billing_rate) if s.tapo_billing_rate is not None else 10.0
        room = rooms.get(str(s.room_id)) if s.room_id else None
        room_name = room.name.strip() if room else (s.name.strip() if s.name else dev_id)

        # Categorization
        r_lower = room_name.lower()
        if "freezer" in r_lower:
            cat = "Freezer"
        elif "fridge" in r_lower:
            cat = "Fridge"
        elif "miso" in r_lower or "vinager" in r_lower or "vinegar" in r_lower or "ferment" in r_lower:
            cat = "Fermentation"
        else:
            cat = "General"

        # Check live eWeLink cloud data first for current month
        live_data = live_ewelink_map.get(dev_id) if is_current_month else None
        energy_kwh = 0.0

        if live_data and live_data.get("month_kwh") is not None and live_data["month_kwh"] > 0:
            energy_kwh = float(live_data["month_kwh"])
            latest_power_w = float(live_data.get("power_w") or 0.0)
            latest_voltage_v = float(live_data.get("voltage_v") or 230.0)
            latest_current_a = float(live_data.get("current_a") or 0.0)
            status = "online" if live_data.get("online") and latest_power_w > 1.0 else ("idle" if live_data.get("online") else "offline")

            # Persist live record if latest DB record is older than 5 minutes
            try:
                last_rec = db.query(PlugTelemetry).filter(
                    PlugTelemetry.device_id == dev_id
                ).order_by(PlugTelemetry.timestamp.desc()).first()
                if not last_rec or (now_utc - last_rec.timestamp).total_seconds() > 300:
                    new_pt = PlugTelemetry(
                        device_id=dev_id,
                        apower=latest_power_w,
                        voltage=float(live_data.get("voltage_v") or 230.0),
                        current=float(live_data.get("current_a") or 0.0),
                        today_energy=float(live_data.get("today_kwh") or 0.0),
                        month_energy=energy_kwh,
                        timestamp=now_utc
                    )
                    db.add(new_pt)
                    db.commit()
            except Exception as e:
                db.rollback()
                logger.error(f"Error persisting live telemetry for {dev_id}: {e}")
        else:
            # Fallback to database records
            row = db.query(
                func.max(PlugTelemetry.month_energy).label("max_m"),
                func.max(PlugTelemetry.today_energy).label("max_t")
            ).filter(
                PlugTelemetry.device_id == dev_id,
                PlugTelemetry.timestamp >= start_dt,
                PlugTelemetry.timestamp <= end_dt
            ).first()

            energy_kwh = float(row.max_m or 0.0) if row else 0.0
            if energy_kwh <= 0.0:
                d_sum = db.execute(text("""
                    SELECT sum(day_max) FROM (
                        SELECT max(today_energy) as day_max
                        FROM monitoring.plug_telemetry
                        WHERE device_id = :dev_id AND timestamp >= :s_dt AND timestamp <= :e_dt
                        GROUP BY timestamp::date
                    ) sub
                """), {"dev_id": dev_id, "s_dt": start_dt, "e_dt": end_dt}).scalar()
                energy_kwh = float(d_sum or 0.0)

            latest_tel = db.query(PlugTelemetry).filter(
                PlugTelemetry.device_id == dev_id
            ).order_by(PlugTelemetry.timestamp.desc()).first()

            latest_power_w = float(latest_tel.apower or 0.0) if latest_tel else 0.0
            latest_voltage_v = float(latest_tel.voltage or 230.0) if latest_tel and latest_tel.voltage else 230.0
            latest_current_a = float(latest_tel.current or 0.0) if latest_tel and latest_tel.current else 0.0
            is_recent = latest_tel and (now_utc - latest_tel.timestamp).total_seconds() < 1800
            if is_recent and latest_power_w > 1.0:
                status = "online"
            elif is_recent:
                status = "idle"
            else:
                status = "offline"

        if status == "online":
            active_device_count += 1
            combined_live_power_w += latest_power_w

        # Query previous month energy for MoM delta
        prev_row = db.query(
            func.max(PlugTelemetry.month_energy).label("max_m")
        ).filter(
            PlugTelemetry.device_id == dev_id,
            PlugTelemetry.timestamp >= prev_start_dt,
            PlugTelemetry.timestamp <= prev_end_dt
        ).first()
        prev_dev_energy = float(prev_row.max_m or 0.0) if prev_row else 0.0
        if prev_dev_energy <= 0.0:
            prev_d_sum = db.execute(text("""
                SELECT sum(day_max) FROM (
                    SELECT max(today_energy) as day_max
                    FROM monitoring.plug_telemetry
                    WHERE device_id = :dev_id AND timestamp >= :s_dt AND timestamp <= :e_dt
                    GROUP BY timestamp::date
                ) sub
            """), {"dev_id": dev_id, "s_dt": prev_start_dt, "e_dt": prev_end_dt}).scalar()
            prev_dev_energy = float(prev_d_sum or 0.0)

        prev_dev_cost = round(prev_dev_energy * rate, 2)
        prev_total_cost += prev_dev_cost

        # Calculate MoM deltas for this device
        if prev_dev_energy > 0.0:
            mom_kwh_delta_pct = round(((energy_kwh - prev_dev_energy) / prev_dev_energy) * 100.0, 1)
            mom_cost_delta = round((energy_kwh * rate) - prev_dev_cost, 2)
        else:
            mom_kwh_delta_pct = None
            mom_cost_delta = None

        dev_cost = round(energy_kwh * rate, 2)
        total_factory_energy += energy_kwh
        total_factory_cost += dev_cost

        daily_avg_kwh = round(energy_kwh / days_elapsed, 3)
        daily_avg_cost = round(dev_cost / days_elapsed, 2)

        devices_list.append({
            "device_id": dev_id,
            "device_name": room_name,
            "sensor_name": s.name,
            "room_id": str(s.room_id) if s.room_id else None,
            "room_name": room_name,
            "category": cat,
            "hardware_model": "Sonoff POWR320D",
            "billing_rate": rate,
            "energy_kwh": round(energy_kwh, 3),
            "cost": dev_cost,
            "cost_percentage": 0.0,
            "energy_percentage": 0.0,
            "daily_avg_kwh": daily_avg_kwh,
            "daily_avg_cost": daily_avg_cost,
            "latest_power_w": round(latest_power_w, 1),
            "voltage_v": round(latest_voltage_v, 1),
            "current_a": round(latest_current_a, 2),
            "status": status,
            "mom_kwh_delta_pct": mom_kwh_delta_pct,
            "mom_cost_delta": mom_cost_delta
        })

    # Fill percentage shares & sort descending by cost
    for d in devices_list:
        d["cost_percentage"] = round((d["cost"] / total_factory_cost * 100.0), 1) if total_factory_cost > 0 else 0.0
        d["energy_percentage"] = round((d["energy_kwh"] / total_factory_energy * 100.0), 1) if total_factory_energy > 0 else 0.0

    devices_list.sort(key=lambda x: x["cost"], reverse=True)

    # 5. Category Roll-up Aggregation
    cat_map = {}
    for d in devices_list:
        c = d["category"]
        if c not in cat_map:
            cat_map[c] = {"category": c, "device_count": 0, "energy_kwh": 0.0, "cost": 0.0}
        cat_map[c]["device_count"] += 1
        cat_map[c]["energy_kwh"] += d["energy_kwh"]
        cat_map[c]["cost"] += d["cost"]

    categories_list = []
    for c_name, c_data in cat_map.items():
        pct = round((c_data["cost"] / total_factory_cost * 100.0), 1) if total_factory_cost > 0 else 0.0
        categories_list.append(MonthlyCategoryRollup(
            category=c_name,
            device_count=c_data["device_count"],
            energy_kwh=round(c_data["energy_kwh"], 2),
            cost=round(c_data["cost"], 2),
            percentage=pct
        ))
    categories_list.sort(key=lambda x: x.cost, reverse=True)

    # 6. Daily cumulative trend points
    daily_rows = db.execute(text("""
        SELECT d.day::date as date_val, sum(d.day_energy) as total_kwh
        FROM (
            SELECT device_id, timestamp::date as day, max(today_energy) as day_energy
            FROM monitoring.plug_telemetry
            WHERE timestamp >= :s_dt AND timestamp <= :e_dt
            GROUP BY device_id, timestamp::date
        ) d
        GROUP BY d.day
        ORDER BY d.day ASC
    """), {"s_dt": start_dt, "e_dt": end_dt}).fetchall()

    daily_trends = []
    running_kwh = 0.0
    running_cost = 0.0
    for r in daily_rows:
        day_kwh = float(r.total_kwh or 0.0)
        avg_rate = (total_factory_cost / total_factory_energy) if total_factory_energy > 0 else 10.0
        day_cost = round(day_kwh * avg_rate, 2)
        running_kwh += day_kwh
        running_cost += day_cost
        d_str = r.date_val.strftime("%Y-%m-%d")
        daily_trends.append(DailyTrendPoint(
            day=r.date_val.strftime("%d"),
            date=d_str,
            energy_kwh=round(day_kwh, 2),
            cost=day_cost,
            cumulative_kwh=round(running_kwh, 2),
            cumulative_cost=round(running_cost, 2)
        ))

    # 7. Projected Month-End Forecast
    if is_current_month:
        projected_cost = round((total_factory_cost / days_elapsed) * days_in_month, 2) if days_elapsed > 0 else total_factory_cost
        projected_kwh = round((total_factory_energy / days_elapsed) * days_in_month, 2) if days_elapsed > 0 else total_factory_energy
    else:
        projected_cost = total_factory_cost
        projected_kwh = total_factory_energy

    # 8. MoM total cost comparison
    mom_cost_change_pct = None
    if prev_total_cost > 0.0:
        mom_cost_change_pct = round(((total_factory_cost - prev_total_cost) / prev_total_cost) * 100.0, 1)

    # 9. Highest consumer highlight
    highest_consumer = None
    if devices_list and devices_list[0]["cost"] > 0:
        h = devices_list[0]
        highest_consumer = {
            "device_id": h["device_id"],
            "device_name": h["device_name"],
            "cost": h["cost"],
            "energy_kwh": h["energy_kwh"],
            "percentage": h["cost_percentage"]
        }

    # 10. Smart Anomaly & Insight Generation
    insights = []
    if highest_consumer and highest_consumer["percentage"] >= 25.0:
        insights.append(
            f"⚠️ Heavy Consumer: {highest_consumer['device_name']} accounts for {highest_consumer['percentage']}% of the total factory power bill (₹ {highest_consumer['cost']:.2f})."
        )

    for d in devices_list:
        if d.get("mom_kwh_delta_pct") and d["mom_kwh_delta_pct"] >= 30.0:
            insights.append(
                f"📈 Consumption Surge: {d['device_name']} power usage increased by {d['mom_kwh_delta_pct']}% compared to {datetime(prev_year, prev_month_int, 1).strftime('%B')}. Inspect door seal gasket or compressor cycling."
            )

    if total_factory_cost > 0:
        savings_potential = round(total_factory_cost * 0.08, 2)
        insights.append(
            f"💡 Efficiency Opportunity: Optimizing defrost timers and thermostat setpoints could reduce factory expenses by ~₹ {savings_potential:.2f}/month."
        )

    summary_obj = MonthlySummaryAggregate(
        total_energy_kwh=round(total_factory_energy, 2),
        total_cost=round(total_factory_cost, 2),
        prev_month_cost=round(prev_total_cost, 2) if prev_total_cost > 0 else None,
        mom_cost_change_pct=mom_cost_change_pct,
        projected_month_end_cost=projected_cost,
        projected_month_end_kwh=projected_kwh,
        daily_avg_cost=round(total_factory_cost / days_elapsed, 2),
        daily_avg_kwh=round(total_factory_energy / days_elapsed, 2),
        combined_live_power_w=round(combined_live_power_w, 1),
        device_count=len(devices_list),
        active_device_count=active_device_count,
        highest_consumer=highest_consumer,
        currency="₹"
    )

    return MonthlyCostSummaryResponse(
        selected_month=target_month,
        month_name=month_name,
        available_months=available_months,
        is_current_month=is_current_month,
        summary=summary_obj,
        categories=categories_list,
        daily_trends=daily_trends,
        devices=[MonthlyDeviceBreakdown(**d) for d in devices_list],
        insights=insights
    )


@router.post("/plugs/billing-rate")
def update_plug_billing_rate(req: BillingRateUpdateRequest, db: Session = Depends(get_db)):
    """Update electricity billing rate (₹/kWh) for a specific Sonoff device or all devices."""
    if req.billing_rate <= 0:
        raise HTTPException(status_code=400, detail="Billing rate must be greater than 0.")

    if req.apply_to_all:
        sensors = db.query(Sensor).filter(Sensor.type == "plug").all()
        for s in sensors:
            s.tapo_billing_rate = req.billing_rate
        db.commit()
        return {"message": f"Updated billing rate to ₹{req.billing_rate}/kWh for all {len(sensors)} appliances."}
    elif req.device_id:
        sensor = db.query(Sensor).filter(Sensor.device_id == req.device_id, Sensor.type == "plug").first()
        if not sensor:
            sensor = db.query(Sensor).filter(Sensor.device_id == req.device_id).first()
        if not sensor:
            raise HTTPException(status_code=404, detail="Device not found.")
        sensor.tapo_billing_rate = req.billing_rate
        db.commit()
        return {"message": f"Updated billing rate for {sensor.name or req.device_id} to ₹{req.billing_rate}/kWh."}
    else:
        raise HTTPException(status_code=400, detail="Specify device_id or set apply_to_all=True.")


@router.get("/plugs/monthly-summary/export")
async def export_monthly_cost_csv(month: Optional[str] = None, db: Session = Depends(get_db)):
    """Export the full monthly power and electricity cost summary as a structured CSV."""
    data = await get_plugs_monthly_summary(month=month, db=db)
    output = StringIO()
    writer = csv.writer(output)

    writer.writerow([f"Ground Up Factory — Monthly Power & Cost Ledger ({data.month_name})"])
    writer.writerow([])
    writer.writerow(["Total Factory Electricity Bill (₹)", f"₹ {data.summary.total_cost:.2f}"])
    writer.writerow(["Total Energy Consumed (kWh)", f"{data.summary.total_energy_kwh:.2f} kWh"])
    writer.writerow(["Daily Average Cost (₹)", f"₹ {data.summary.daily_avg_cost:.2f}"])
    writer.writerow(["Daily Average Energy (kWh)", f"{data.summary.daily_avg_kwh:.2f} kWh"])
    writer.writerow(["Combined Live Load (W)", f"{data.summary.combined_live_power_w:.1f} W"])
    writer.writerow(["Monitored Devices", str(data.summary.device_count)])
    writer.writerow([])
    writer.writerow(["Rank", "Appliance / Room", "Sonoff Device ID", "Category", "Monthly Energy (kWh)", "Tariff Rate (₹/kWh)", "Total Cost (₹)", "Share (%)", "Daily Avg (₹)", "Live Power (W)", "Status"])

    for idx, d in enumerate(data.devices, start=1):
        writer.writerow([
            idx,
            d.device_name,
            d.device_id,
            d.category,
            f"{d.energy_kwh:.3f}",
            f"{d.billing_rate:.2f}",
            f"{d.cost:.2f}",
            f"{d.cost_percentage:.1f}%",
            f"{d.daily_avg_cost:.2f}",
            f"{d.latest_power_w:.1f}",
            d.status.upper()
        ])

    writer.writerow([])
    writer.writerow(["Department Roll-Up", "Device Count", "Total Energy (kWh)", "Total Cost (₹)", "Share (%)"])
    for cat in data.categories:
        writer.writerow([cat.category, cat.device_count, f"{cat.energy_kwh:.2f}", f"{cat.cost:.2f}", f"{cat.percentage:.1f}%"])

    output.seek(0)
    filename = f"Sonoff_Monthly_Power_Report_{data.selected_month}.csv"
    response = StreamingResponse(iter([output.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response


@router.post("/plugs/monthly-summary/whatsapp")
async def dispatch_monthly_cost_whatsapp(month: Optional[str] = None, db: Session = Depends(get_db)):
    """Format and dispatch the monthly electricity cost summary via WhatsApp."""
    import urllib.parse
    data = await get_plugs_monthly_summary(month=month, db=db)

    top_devices = [d for d in data.devices if d.cost > 0][:5]
    top_lines = []
    for idx, d in enumerate(top_devices, 1):
        top_lines.append(f"{idx}. *{d.device_name.strip()}*: ₹ {d.cost:,.2f} ({d.energy_kwh:,.1f} kWh · {d.cost_percentage:.1f}%)")

    now_day = datetime.utcnow().day
    period_title = f"{data.month_name} (MTD · Day {now_day})" if data.is_current_month else data.month_name

    msg_body = (
        f"⚡ *GROUND UP FACTORY — MONTHLY POWER REPORT*\n"
        f"📅 *Period*: {period_title}\n"
        + (
            f"💰 *Current Bill (MTD)*: ₹ {data.summary.total_cost:,.2f}\n"
            f"🔮 *Projected Month-End*: ₹ {data.summary.projected_month_end_cost:,.2f}\n"
            f"⚡ *Energy Consumed (MTD)*: {data.summary.total_energy_kwh:,.2f} kWh\n"
            f"📊 *Daily Run Rate*: ₹ {data.summary.daily_avg_cost:.2f} / day ({data.summary.daily_avg_kwh:.1f} kWh/day)\n"
            if data.is_current_month else
            f"💰 *Total Electricity Bill*: ₹ {data.summary.total_cost:,.2f}\n"
            f"⚡ *Total Energy Consumed*: {data.summary.total_energy_kwh:,.2f} kWh\n"
            f"📊 *Daily Avg Expense*: ₹ {data.summary.daily_avg_cost:.2f} / day\n"
        )
        + (f"⚡ *Live Factory Load*: {data.summary.combined_live_power_w:,.0f} W\n" if (data.is_current_month and data.summary.combined_live_power_w > 0) else "")
        + f"🔌 *Appliances Monitored*: {data.summary.device_count} (Sonoff POWR320D)\n\n"
        + "*Top Power Consumers:*\n"
        + "\n".join(top_lines)
        + "\n\n_Generated from Ground Up Monitoring Platform._"
    )

    dispatched = False
    recipients_count = 0
    try:
        from groundup_webhooks.recipient_resolver import resolve_monitoring_recipients
        from groundup_webhooks.whatsapp_client import send_whatsapp_text_sync
        recipients = resolve_monitoring_recipients(db, target_groups=[1, 2])
        for r in recipients:
            phone = r.get("phone_number")
            if phone:
                res = send_whatsapp_text_sync(phone, msg_body, db, sender_name="GroundUp Bot")
                if res:
                    recipients_count += 1
                    dispatched = True
    except Exception as e:
        logger.error(f"Error dispatching WhatsApp monthly summary: {e}")

    encoded_msg = urllib.parse.quote(msg_body)
    return {
        "success": True,
        "message": f"Monthly report dispatched to {recipients_count} recipient(s)." if dispatched else "Monthly summary formatted.",
        "whatsapp_text": msg_body,
        "wa_link": f"https://wa.me/?text={encoded_msg}"
    }


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


from pydantic import BaseModel
class DiscoveredPlugsRequest(BaseModel):
    devices: list

@router.post("/tapo/discovered", tags=["Edge"])
def tapo_discovered(
    req: DiscoveredPlugsRequest,
    api_key: str = Depends(verify_edge_api_key)
):
    """Receive discovered Tapo plugs from Edge Agent."""
    now = datetime.utcnow()
    for d in req.devices:
        ip = d.get("ip")
        if ip:
            _DISCOVERED_TAPO_PLUGS[ip] = {
                "ip": ip,
                "model": d.get("model", "Unknown"),
                "mac": d.get("mac", "Unknown"),
                "timestamp": now
            }
    return {"status": "ok"}

@router.get("/tapo/discovered", tags=["Monitoring"])
def get_tapo_discovered(db: Session = Depends(get_db)):
    """Fetch discovered plugs that are not already assigned to active sensors."""
    # Find all Tapo IPs already actively used in DB
    configured_ips = {
        s.tapo_ip for s in db.query(Sensor).filter(
            Sensor.active == True,
            Sensor.tapo_ip.isnot(None)
        ).all()
    }
    
    # Prune stale discoveries (older than 15 minutes) and exclude configured IPs
    now = datetime.utcnow()
    results = []
    keys_to_delete = []
    
    for ip, data in _DISCOVERED_TAPO_PLUGS.items():
        if (now - data["timestamp"]).total_seconds() > 900:  # 15 mins
            keys_to_delete.append(ip)
        else:
            is_configured = ip in configured_ips
            results.append({
                "ip": data["ip"],
                "model": data["model"],
                "mac": data["mac"],
                "configured": is_configured,
                "last_seen": data["timestamp"].isoformat()
            })
            
    for k in keys_to_delete:
        del _DISCOVERED_TAPO_PLUGS[k]
        
    return results

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
async def get_device_ai_summary(
    device_id: str,
    days: Optional[str] = "1",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Fetch Gemini AI maintenance summary & diagnostic recommendations for a device or room."""
    from backend.models.room import Room
    from backend.models.plug_telemetry import PlugTelemetry

    sensor = db.query(Sensor).filter(Sensor.device_id == device_id).first()
    if not sensor and device_id:
        sensor = db.query(Sensor).filter(Sensor.device_id == device_id.strip()).first()
    
    room = None
    if sensor and sensor.room_id:
        room = db.query(Room).filter(Room.id == sensor.room_id).first()
    
    if not room:
        room = db.query(Room).filter(Room.id == device_id).first()
        
    if not room and sensor:
        room = db.query(Room).filter(Room.name == sensor.name).first()
        if not room:
            room = Room(id=sensor.room_id or "0", name=sensor.name or device_id, type="fridge")

    if not room:
        room = db.query(Room).first()

    if not room:
        raise HTTPException(status_code=404, detail="Device or Room not found")
    
    # Resolve time range
    ist_offset = timedelta(hours=5, minutes=30)
    
    if start_date and end_date and days == "custom":
        try:
            start_utc = datetime.fromisoformat(start_date) - ist_offset
            end_utc = datetime.fromisoformat(end_date) - ist_offset
        except Exception:
            start_utc = datetime.utcnow() - timedelta(days=1)
            end_utc = datetime.utcnow()
    else:
        try:
            days_int = int(days) if days and days != "custom" else 1
        except (ValueError, TypeError):
            days_int = 1
        start_utc = datetime.utcnow() - timedelta(days=days_int)
        end_utc = datetime.utcnow()

    # Gather all device_ids for this room
    sensors_list = db.query(Sensor).filter(Sensor.room_id == room.id, Sensor.active == True).all() if room.id else ([sensor] if sensor else [])
    all_device_ids = list(set([device_id] + [s.device_id for s in sensors_list if s.device_id]))
    
    # Query telemetry
    logs = db.query(DeviceTelemetry).filter(
        DeviceTelemetry.device_id.in_(all_device_ids),
        DeviceTelemetry.timestamp >= start_utc,
        DeviceTelemetry.timestamp <= end_utc
    ).order_by(DeviceTelemetry.timestamp.asc()).all()
    
    plug_logs = db.query(PlugTelemetry).filter(
        PlugTelemetry.device_id.in_(all_device_ids),
        PlugTelemetry.timestamp >= start_utc,
        PlugTelemetry.timestamp <= end_utc
    ).order_by(PlugTelemetry.timestamp.asc()).all()

    # Compute stats
    temps = [float(l.temperature) for l in logs if l.temperature is not None]
    hums = [float(l.humidity) for l in logs if l.humidity is not None]
    
    temp_sensor = next((s for s in sensors_list if s.type == "temperature"), sensor)
    temp_min_th = float(temp_sensor.min_threshold) if temp_sensor and temp_sensor.min_threshold is not None else None
    temp_max_th = float(temp_sensor.max_threshold) if temp_sensor and temp_sensor.max_threshold is not None else None
    has_plug = temp_sensor is not None and temp_sensor.tapo_ip is not None and len(str(getattr(temp_sensor, 'tapo_ip', '') or '').strip()) > 0
    
    stats = {
        "room_name": room.name,
        "room_type": room.type or "room",
        "readings_count": len(logs),
        "temp_avg": round(sum(temps) / len(temps), 2) if temps else None,
        "temp_min": round(min(temps), 2) if temps else None,
        "temp_max": round(max(temps), 2) if temps else None,
        "temp_min_threshold": temp_min_th,
        "temp_max_threshold": temp_max_th,
        "hum_avg": round(sum(hums) / len(hums), 1) if hums else None,
        "hum_min": round(min(hums), 1) if hums else None,
        "hum_max": round(max(hums), 1) if hums else None,
    }
    
    # Breach hours
    above_hours = 0.0
    below_hours = 0.0
    if temps and len(logs) > 1:
        for i in range(1, len(logs)):
            t = float(logs[i].temperature) if logs[i].temperature is not None else None
            if t is None:
                continue
            delta_h = (logs[i].timestamp - logs[i-1].timestamp).total_seconds() / 3600.0
            if delta_h > 1.0:
                continue
            if temp_max_th is not None and t > temp_max_th:
                above_hours += delta_h
            if temp_min_th is not None and t < temp_min_th:
                below_hours += delta_h
    
    stats["above_max_hours"] = round(above_hours, 2)
    stats["below_min_hours"] = round(below_hours, 2)

    # Plug stats
    if plug_logs:
        powers = [float(l.apower) for l in plug_logs if l.apower is not None]
        voltages = [float(l.voltage) for l in plug_logs if l.voltage is not None]
        currents = [float(l.current) for l in plug_logs if l.current is not None]
        energies = [float(l.today_energy) for l in plug_logs if l.today_energy is not None]
        
        threshold = float(temp_sensor.tapo_running_threshold) if (temp_sensor and temp_sensor.tapo_running_threshold is not None) else 80.0
        runtime_hours = 0.0
        starts_count = 0
        was_running = False
        for i in range(1, len(plug_logs)):
            p = float(plug_logs[i].apower) if plug_logs[i].apower is not None else 0.0
            running = p >= threshold
            if running and not was_running:
                starts_count += 1
            if running:
                delta = (plug_logs[i].timestamp - plug_logs[i-1].timestamp).total_seconds() / 3600.0
                if delta < 0.25:
                    runtime_hours += delta
            was_running = running
        
        stats["has_plug"] = True
        stats["power_avg_w"] = round(sum(powers) / len(powers), 1) if powers else 0.0
        stats["power_max_w"] = round(max(powers), 1) if powers else 0.0
        stats["voltage_avg_v"] = round(sum(voltages) / len(voltages), 1) if voltages else 0.0
        stats["current_avg_a"] = round(sum(currents) / len(currents), 3) if currents else 0.0
        stats["runtime_hours"] = round(runtime_hours, 2)
        stats["compressor_starts"] = starts_count
        stats["energy_kwh"] = round(max(energies) / 1000.0, 3) if energies else 0.0
        stats["plug_readings_count"] = len(plug_logs)
    else:
        stats["has_plug"] = has_plug

    # 7-day baselines
    baseline_cutoff = datetime.utcnow() - timedelta(days=7)
    baseline_temps = db.query(func.avg(DeviceTelemetry.temperature)).filter(
        DeviceTelemetry.device_id.in_(all_device_ids),
        DeviceTelemetry.timestamp >= baseline_cutoff
    ).scalar()
    stats["baseline_7d_temp_avg"] = round(float(baseline_temps), 2) if baseline_temps else None

    # Determine timeframe label
    delta_days = (end_utc - start_utc).total_seconds() / 86400.0
    tf_label = f"{round(delta_days)}-Day" if delta_days >= 1 else f"{round(delta_days * 24)}h"

    # Build dedicated Gemini prompt for rich per-device diagnostics
    import json as json_mod
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {
            "room_name": room.name,
            "status": "healthy",
            "analysis": f"AI service not configured. {room.name} had avg temp {stats.get('temp_avg', 'N/A')}°C over {tf_label}.",
            "action_items": []
        }

    prompt = f"""You are the AI Diagnostics Engine for the Ground Up Cold Storage factory in Pune, India.
    
    Analyze the following telemetry data for "{room.name}" ({room.type or 'room'}) over the {tf_label} timeframe.
    
    Telemetry Summary:
    {json_mod.dumps(stats, indent=2)}
    
    INSTRUCTIONS:
    1. Write a comprehensive, detailed multi-sentence engineering analysis (at least 3-4 sentences) that:
       - States the average, minimum, and maximum temperatures with their values
       - Compares them against the configured thresholds ({temp_min_th}°C to {temp_max_th}°C)
       - Reports breach hours (time above max or below min) if any
       - If smart plug data is available, correlates power draw (watts), compressor runtime (hours), cycle count (starts), and energy consumption (kWh)
       - Compares against the 7-day baseline average temperature of {stats.get('baseline_7d_temp_avg', 'N/A')}°C
       - Comments on thermal stability, compressor health, and energy efficiency
       - Mentions humidity trends if data is available
    2. Provide 3-5 specific, actionable recommendations for the factory maintenance team.
    3. Set status to "healthy" if within thresholds, "warning" if marginal, "critical" if breaching.

    Format your output strictly as a JSON object:
    {{
      "room_name": "{room.name}",
      "status": "healthy" | "warning" | "critical",
      "analysis": "Your comprehensive multi-sentence engineering analysis here.",
      "action_items": [
        "Specific recommendation 1",
        "Specific recommendation 2",
        "Specific recommendation 3"
      ]
    }}
    """
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json"
        }
    }
    
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=payload, timeout=30.0)
            if res.status_code == 200:
                text = res.json()["candidates"][0]["content"]["parts"][0]["text"]
                cleaned = text.strip()
                if cleaned.startswith("```json"):
                    cleaned = cleaned[7:]
                elif cleaned.startswith("```"):
                    cleaned = cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()
                
                result = json_mod.loads(cleaned)
                if "room_name" not in result:
                    result["room_name"] = room.name
                return result
            else:
                logger.error(f"Gemini AI-summary returned {res.status_code}: {res.text[:300]}")
    except Exception as e:
        logger.error(f"AI-summary Gemini call failed: {e}", exc_info=True)
    
    # Fallback: generate rule-based response with actual stats
    fb_status = "healthy"
    fb_parts = []
    if stats.get("temp_avg") is not None:
        fb_parts.append(f"The {room.name} maintained an average temperature of {stats['temp_avg']}°C (min {stats['temp_min']}°C, max {stats['temp_max']}°C) over the {tf_label} period.")
    if temp_min_th is not None and temp_max_th is not None:
        if above_hours > 0.25:
            fb_status = "warning" if above_hours < 2 else "critical"
            fb_parts.append(f"Temperature exceeded the upper threshold of {temp_max_th}°C for {above_hours:.1f} hours.")
        elif below_hours > 0.25:
            fb_status = "warning" if below_hours < 2 else "critical"
            fb_parts.append(f"Temperature dropped below the lower threshold of {temp_min_th}°C for {below_hours:.1f} hours.")
        else:
            fb_parts.append(f"All readings remained within the target range of {temp_min_th}°C to {temp_max_th}°C with zero breach hours.")
    if stats.get("has_plug") and stats.get("power_avg_w") is not None:
        fb_parts.append(f"Smart plug reported average power draw of {stats['power_avg_w']}W, compressor runtime of {stats.get('runtime_hours', 0)}h with {stats.get('compressor_starts', 0)} start cycles, consuming {stats.get('energy_kwh', 0)} kWh.")
    if stats.get("baseline_7d_temp_avg") is not None:
        fb_parts.append(f"The 7-day baseline average is {stats['baseline_7d_temp_avg']}°C.")
    
    return {
        "room_name": room.name,
        "status": fb_status,
        "analysis": " ".join(fb_parts) if fb_parts else "Thermal telemetry operating within expected limits.",
        "action_items": [
            "Continue monitoring temperature trends for any drift from baseline.",
            "Verify sensor battery levels and connectivity status.",
            "Inspect door seals and gaskets for wear during next maintenance cycle."
        ]
    }


# ─── ASK ME AI CHAT & DOWNLOAD CHANNELS ───

from pydantic import BaseModel
import httpx
import json

class ChatRequest(BaseModel):
    message: str
    timezone_offset_minutes: Optional[int] = -330  # Default to IST (UTC+5:30)
    current_device_id: Optional[str] = None

def _clean_json_text(text: str) -> str:
    """Safely strip markdown code fences from JSON output if present."""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()

def _regex_parse_json(text: str) -> dict:
    """Fallback parser using regex and bounding search if standard json.loads fails."""
    import re
    result = {}
    
    # 1. Bounded search for "answer" field
    idx_answer = text.find('"answer"')
    if idx_answer != -1:
        start_val = text.find('"', idx_answer + 8)
        if start_val != -1:
            start_val += 1
            end_bound = text.find('"is_report_requested"')
            if end_bound != -1:
                val_text = text[start_val:end_bound].strip()
                if val_text.endswith(','):
                    val_text = val_text[:-1].strip()
                if val_text.endswith('"'):
                    val_text = val_text[:-1].strip()
                result["answer"] = val_text.replace('\\"', '"').replace('\\n', '\n')
    
    if "answer" not in result:
        # Simple regex fallback for answer
        answer_match = re.search(r'"answer"\s*:\s*"(.*?)"\s*(?:,|\})', text, re.DOTALL)
        if answer_match:
            result["answer"] = answer_match.group(1).replace('\\"', '"').replace('\\n', '\n')
            
    # 2. Extract boolean and string fields
    is_report_match = re.search(r'"is_report_requested"\s*:\s*(true|false)', text, re.IGNORECASE)
    if is_report_match:
        result["is_report_requested"] = is_report_match.group(1).lower() == "true"
        
    start_time_match = re.search(r'"report_start_time"\s*:\s*"(.*?)"', text)
    if start_time_match:
        val = start_time_match.group(1)
        result["report_start_time"] = None if val.lower() == "null" or not val else val
        
    end_time_match = re.search(r'"report_end_time"\s*:\s*"(.*?)"', text)
    if end_time_match:
        val = end_time_match.group(1)
        result["report_end_time"] = None if val.lower() == "null" or not val else val
        
    format_match = re.search(r'"report_format"\s*:\s*"(.*?)"', text)
    if format_match:
        val = format_match.group(1)
        result["report_format"] = None if val.lower() == "null" or not val else val
        
    return result

async def _call_gemini_json(prompt: str) -> dict:
    """Helper to query Gemini API and enforce JSON response."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"error": "GEMINI_API_KEY is not configured on the server."}
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json"
        }
    }
    
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=payload, timeout=20.0)
            if res.status_code == 200:
                text = res.json()["candidates"][0]["content"]["parts"][0]["text"]
                cleaned_text = _clean_json_text(text)
                try:
                    data = json.loads(cleaned_text)
                    if isinstance(data, dict) and "answer" not in data:
                        if "analysis" in data:
                            data["answer"] = str(data["analysis"])
                        elif "summary" in data:
                            data["answer"] = str(data["summary"])
                        elif "device_analysis" in data:
                            da = data["device_analysis"]
                            if isinstance(da, dict):
                                summary = da.get("assessment", {}).get("summary", "") or da.get("summary", "")
                                findings = "\n• " + "\n• ".join(da.get("assessment", {}).get("key_findings", [])) if da.get("assessment", {}).get("key_findings") else ""
                                recs = "\n\nRecommendations:\n• " + "\n• ".join(da.get("recommendations", [])) if da.get("recommendations") else ""
                                data["answer"] = f"{summary}{findings}{recs}".strip()
                            else:
                                data["answer"] = str(da)
                    return data
                except Exception as json_err:
                    logger.warning(f"Standard JSON parsing failed: {json_err}. Attempting regex fallback parsing on: {cleaned_text}")
                    parsed = _regex_parse_json(cleaned_text)
                    if parsed and "answer" in parsed:
                        return parsed
                    raise json_err
            else:
                return {"error": f"Gemini API returned code {res.status_code}: {res.text}"}
    except Exception as e:
        return {"error": f"Failed to call Gemini API: {str(e)}"}

@router.post("/chat")
async def chat_with_sensors(req: ChatRequest, db: Session = Depends(get_db)):
    """
    Handles natural language queries about temperature/humidity data.
    """
    # 1. Gather configured rooms & active sensors to guide extraction
    from backend.models.room import Room
    rooms = db.query(Room).filter(Room.active == True).all()
    sensors = db.query(Sensor).filter(Sensor.active == True).all()
    
    # 2. Match target room/device from the message text or current fallback
    device_id = None
    room_name = "Sensor"
    import re
    msg_tokens = set(re.findall(r'\w+', req.message.lower()))
    
    best_score = 0
    best_room = None
    
    for r in rooms:
        r_name_lower = r.name.lower()
        if r_name_lower in req.message.lower():
            best_room = r
            best_score = 100
            break
        
        # Keyword token match (e.g. 'miso', 'vinegar', 'terrace', 'freezer', 'samsung', 'black', 'wild')
        room_tokens = set(re.findall(r'\w+', r_name_lower)) - {"room", "the", "and", "zone"}
        common = room_tokens.intersection(msg_tokens)
        if common:
            score = len(common) * 10
            if score > best_score:
                best_score = score
                best_room = r
                
    if best_room:
        r_sensors = [s for s in sensors if s.room_id == best_room.id and s.type == "temperature"]
        if not r_sensors:
            r_sensors = [s for s in sensors if s.room_id == best_room.id]
        if r_sensors:
            device_id = r_sensors[0].device_id
            room_name = best_room.name
                 
    if not device_id and req.current_device_id:
        device_id = req.current_device_id
        matched_room = db.query(Room).join(Sensor).filter(Sensor.device_id == device_id).first()
        if matched_room:
            room_name = matched_room.name

    if not device_id:
        # Final fallback to first active sensor
        fallback_sensor = db.query(Sensor).filter(Sensor.active == True).first()
        if fallback_sensor:
            device_id = fallback_sensor.device_id
            
    if not device_id:
        return {"response": "No active sensors could be identified to fetch data from."}

    # 3. Pre-fetch last 48 hours of telemetry data & Tapo Plug telemetry for context
    ist_offset = timedelta(minutes=abs(req.timezone_offset_minutes))
    cutoff_utc = datetime.utcnow() - timedelta(hours=48)
    
    logs = db.query(DeviceTelemetry).filter(
        DeviceTelemetry.device_id == device_id,
        DeviceTelemetry.timestamp >= cutoff_utc
    ).order_by(DeviceTelemetry.timestamp.desc()).all()

    from backend.models.plug_telemetry import PlugTelemetry
    
    msg_lower = req.message.lower()
    is_plug_query = any(k in msg_lower for k in ["plug", "tapo", "power", "watt", "energy", "voltage", "current", "apower", "kwh", "amp"])

    plug_logs = db.query(PlugTelemetry).filter(
        PlugTelemetry.device_id == device_id,
        PlugTelemetry.timestamp >= cutoff_utc
    ).order_by(PlugTelemetry.timestamp.desc()).all()

    # Fallback if specific room has no plug but user asks for plug data
    if is_plug_query and not plug_logs:
        plug_logs = db.query(PlugTelemetry).filter(
            PlugTelemetry.timestamp >= cutoff_utc
        ).order_by(PlugTelemetry.timestamp.desc()).all()

    # Summarize stats
    temps = [float(l.temperature) for l in logs if l.temperature is not None]
    hums = [float(l.humidity) for l in logs if l.humidity is not None]
    summary_stats = {
        "temp_avg": round(sum(temps)/len(temps), 2) if temps else "N/A",
        "temp_min": min(temps) if temps else "N/A",
        "temp_max": max(temps) if temps else "N/A",
        "hum_avg": round(sum(hums)/len(hums), 2) if hums else "N/A",
        "hum_min": min(hums) if hums else "N/A",
        "hum_max": max(hums) if hums else "N/A",
        "total_readings": len(logs)
    }

    plug_context_list = []
    if plug_logs:
        powers = [float(l.apower) for l in plug_logs if l.apower is not None]
        voltages = [float(l.voltage) for l in plug_logs if l.voltage is not None]
        energies = [float(l.today_energy) for l in plug_logs if l.today_energy is not None]
        max_energy_wh = max(energies) if energies else 0.0
        energy_kwh = round(max_energy_wh / 1000.0 if max_energy_wh > 500 else max_energy_wh, 3)
        
        summary_stats["tapo_plug_telemetry"] = {
            "has_plug": True,
            "power_avg_w": round(sum(powers)/len(powers), 1) if powers else 0.0,
            "power_max_w": round(max(powers), 1) if powers else 0.0,
            "voltage_avg_v": round(sum(voltages)/len(voltages), 1) if voltages else 0.0,
            "today_energy_kwh": energy_kwh,
            "plug_readings_count": len(plug_logs)
        }
        
        for pl in plug_logs[:60]:
            local_time = pl.timestamp + ist_offset
            plug_context_list.append({
                "time_ist": local_time.strftime('%Y-%m-%d %I:%M:%S %p'),
                "active_power_w": float(pl.apower) if pl.apower is not None else 0.0,
                "voltage_v": float(pl.voltage) if pl.voltage is not None else 0.0,
                "current_a": float(pl.current) if pl.current is not None else 0.0,
                "today_energy_kwh": float(pl.today_energy) if pl.today_energy is not None else 0.0
            })

    # Format recent sample data (last 80 rows)
    data_context = []
    for log in logs[:80]:
        local_time = log.timestamp + ist_offset
        
        row_dict = {
            "time_ist": local_time.strftime('%Y-%m-%d %I:%M:%S %p'),
            "temperature": float(log.temperature) if log.temperature is not None else None,
            "humidity": float(log.humidity) if log.humidity is not None else None,
            "battery": float(log.battery_level) if log.battery_level is not None else None
        }
            
        data_context.append(row_dict)

    # Calculate current local time for prompt reference
    if req.timezone_offset_minutes < 0:
        local_now = datetime.utcnow() + ist_offset
    else:
        local_now = datetime.utcnow() - ist_offset
    local_now_str = local_now.strftime('%Y-%m-%d %I:%M:%S %p')

    # Construct unified JSON prompt
    prompt = f"""
    You are the AI Chatbot Assistant for the Ground Up Cold Storage factory in Pune, India.
    Current Local Time at the factory (IST) is {local_now_str}.
    
    Target Room: {room_name}
    Target Device ID: {device_id}
    User Query: "{req.message}"
    
    Telemetry Summary: {json.dumps(summary_stats, indent=2)}
    
    Tapo Smart Plug Telemetry Logs (Power W, Voltage V, Current A, Energy kWh):
    {json.dumps(plug_context_list[::-1], indent=2)}
    
    Temperature & Humidity Logs:
    {json.dumps(data_context[::-1], indent=2)}
    
    Formulate a clear response answering their query directly.
    - CRITICAL RULE FOR TAPO PLUG / POWER QUERIES: If the user is asking about Tapo plug data, power draw (Watts), energy consumption (kWh), voltage (V), or current (A), answer strictly using the Tapo Smart Plug Telemetry metrics (active_power_w, voltage_v, today_energy_kwh, current_a). Do NOT present temperature or humidity numbers unless explicitly requested alongside power data.
    - If they ask for a specific time (e.g. "at 10 AM today"), search the logs for the reading closest to that time.
    - If no logs exist, state that politely.
    - If they ask for a download, export, PDF, Excel, or CSV report, set "is_report_requested" to true, and calculate "report_start_time" and "report_end_time" in local factory time (IST) formatted strictly as "YYYY-MM-DDTHH:MM:SS" (ISO 8601 format).
    - CRITICAL RULE FOR 24 HOURS REQUESTS: If the user requests data for "24 hours", "last 24 hours", "yesterday to today", or similar relative 24h ranges, calculate report_start_time as exactly 24 hours before the current local IST time ({local_now_str}), and report_end_time as exactly the current local IST time ({local_now_str}).
    - If they ask for a full day (e.g. "yesterday"), set report_start_time to start of day (00:00:00) and report_end_time to end of day (23:59:59).
    
    Format your response strictly as a JSON object matching this schema:
    {{
      "answer": "Your natural language response here.",
      "is_report_requested": true | false,
      "report_start_time": "YYYY-MM-DDTHH:MM:SS or null",
      "report_end_time": "YYYY-MM-DDTHH:MM:SS or null",
      "report_format": "pdf" | "csv" | null
    }}
    """
    
    result = await _call_gemini_json(prompt)
    if "error" in result:
        return {"response": f"I had trouble parsing your request: {result['error']}"}
        
    answer = result.get("answer") or "I could not generate a response."
    
    # 4. Generate report download link if requested
    report_link = ""
    if result.get("is_report_requested") and result.get("report_start_time"):
        try:
            start_local = datetime.fromisoformat(result["report_start_time"].replace(' ', 'T'))
            end_local = datetime.fromisoformat(result["report_end_time"].replace(' ', 'T'))
            
            start_utc = start_local - ist_offset
            end_utc = end_local - ist_offset
            
            fmt = result.get("report_format") or "csv"
            report_link = f"\n\n📥 **Download Report:** [/sensors/chat/download?device_id={device_id}&start_time_utc={start_utc.isoformat()}&end_time_utc={end_utc.isoformat()}&format={fmt}](download)"
        except Exception as err:
            logger.error(f"Error formulating download URL: {err}")
            
    return {"response": f"{answer}{report_link}"}


@router.get("/chat/download")
def download_chat_report(
    device_id: str,
    start_time_utc: str,
    end_time_utc: str,
    format: str = "pdf",
    db: Session = Depends(get_db)
):
    """
    Generates and returns the PDF or CSV telemetry report (including Tapo Plug telemetry) requested via Chat.
    """
    try:
        start_utc = datetime.fromisoformat(start_time_utc)
        end_utc = datetime.fromisoformat(end_time_utc)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ISO timestamps.")
        
    sensor = db.query(Sensor).filter(Sensor.device_id == device_id).first()
    room_name = "Unknown Room"
    sensor_type = "both"
    device_ids_to_query = [device_id]
    
    if sensor:
        sensor_type = sensor.type
        from backend.models.room import Room
        room = db.query(Room).filter(Room.id == sensor.room_id).first()
        if room:
            room_name = room.name
            room_sensors = db.query(Sensor).filter(Sensor.room_id == room.id, Sensor.active == True).all()
            device_ids_to_query = list(set([device_id] + [s.device_id for s in room_sensors if s.device_id]))

    logs = db.query(DeviceTelemetry).filter(
        DeviceTelemetry.device_id.in_(device_ids_to_query),
        DeviceTelemetry.timestamp >= start_utc,
        DeviceTelemetry.timestamp <= end_utc
    ).order_by(DeviceTelemetry.timestamp.asc()).all()
    
    from backend.models.plug_telemetry import PlugTelemetry
    plug_logs = db.query(PlugTelemetry).filter(
        PlugTelemetry.device_id.in_(device_ids_to_query),
        PlugTelemetry.timestamp >= start_utc,
        PlugTelemetry.timestamp <= end_utc
    ).order_by(PlugTelemetry.timestamp.asc()).all()

    if not logs and not plug_logs:
        logs = db.query(DeviceTelemetry).filter(
            DeviceTelemetry.device_id.in_(device_ids_to_query)
        ).order_by(DeviceTelemetry.timestamp.desc()).limit(100).all()
        logs = list(reversed(logs))

    if not logs and not plug_logs:
        raise HTTPException(status_code=404, detail="No telemetry logs found for the requested timeframe.")
            
    if format == "pdf":
        from backend.services.pdf_generator import generate_telemetry_pdf
        pdf_buffer = generate_telemetry_pdf(
            device_id=device_id,
            room_name=room_name,
            sensor_type=sensor_type,
            start_time=start_utc,
            end_time=end_utc,
            logs=logs
        )
        filename = f"report_{room_name.replace(' ', '_')}_{start_utc.strftime('%Y%m%d')}.pdf"
        headers = {"Content-Disposition": f"attachment; filename={filename}"}
        return StreamingResponse(pdf_buffer, media_type="application/pdf", headers=headers)
        
    else:  # CSV format
        # Index plug logs by timestamp string (minute level)
        plug_by_time = {}
        for pl in plug_logs:
            t_key = pl.timestamp.strftime('%Y-%m-%d %H:%M')
            plug_by_time[t_key] = pl

        output = StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Device ID", 
            "Timestamp (UTC)", 
            "Timestamp (IST)", 
            "Temperature (C)", 
            "Humidity (%)", 
            "Battery (%)",
            "Active Power (W)",
            "Voltage (V)",
            "Current (A)",
            "Today Energy (kWh)"
        ])
        
        ist_offset = timedelta(hours=5, minutes=30)
        for log in logs:
            utc_str = log.timestamp.strftime('%Y-%m-%d %H:%M:%S')
            ist_str = (log.timestamp + ist_offset).strftime('%Y-%m-%d %H:%M:%S')
            t_key = log.timestamp.strftime('%Y-%m-%d %H:%M')
            pl = plug_by_time.get(t_key)
            
            p_watts = str(round(float(pl.apower), 1)) if (pl and pl.apower is not None) else ""
            v_volts = str(round(float(pl.voltage), 1)) if (pl and pl.voltage is not None) else ""
            c_amps = str(round(float(pl.current), 3)) if (pl and pl.current is not None) else ""
            
            today_wh = float(pl.today_energy) if (pl and pl.today_energy is not None) else 0.0
            e_kwh = str(round(today_wh / 1000.0 if today_wh > 500.0 else today_wh, 3)) if pl else ""

            writer.writerow([
                log.device_id,
                utc_str,
                ist_str,
                str(log.temperature) if log.temperature is not None else "",
                str(log.humidity) if log.humidity is not None else "",
                str(log.battery_level) if log.battery_level is not None else "",
                p_watts,
                v_volts,
                c_amps,
                e_kwh
            ])
            
        output.seek(0)
        filename = f"report_{room_name.replace(' ', '_')}_{start_utc.strftime('%Y%m%d')}.csv"
        headers = {"Content-Disposition": f"attachment; filename={filename}"}
        return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers=headers)


