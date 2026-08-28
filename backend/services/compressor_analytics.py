import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.models.sensor import Sensor
from backend.models.room import Room
from backend.models.alert import Alert
from backend.models.setting import Setting
from groundup_webhooks.alert_sender import send_monitoring_alert

logger = logging.getLogger("backend.services.compressor_analytics")

def analyze_compressor_power_stream(
    readings: List[Dict[str, Any]],
    duration_hours: float
) -> Dict[str, Any]:
    """
    Analyzes timeseries power readings using adaptive baseline clustering to accurately
    distinguish idle (fans/boards) from active compressor pumping across inverter & commercial units.
    """
    if not readings or len(readings) < 10:
        return {
            "status": "INSUFFICIENT_DATA",
            "cycle_count": 0,
            "runtime_mins": 0.0,
            "duty_cycle_pct": 0.0,
            "avg_cycle_mins": 0.0,
            "peak_power_watts": 0.0,
            "anomalies": []
        }

    powers = [float(r["power"] or 0.0) for r in readings]
    powers_sorted = sorted(powers)

    p_min = powers_sorted[0]
    p_max = powers_sorted[-1]
    p_15 = powers_sorted[int(len(powers_sorted) * 0.15)]
    p_85 = powers_sorted[int(len(powers_sorted) * 0.85)]

    # Adaptive active threshold calculation
    delta = p_85 - p_15
    if delta >= 25.0:
        active_thresh = p_15 + (delta * 0.35)
    else:
        active_thresh = max(40.0, p_15 + 15.0)

    is_running = False
    cycle_count = 0
    total_running_seconds = 0.0
    current_run_start = None
    run_durations = []
    peak_power = p_max

    for i in range(len(readings)):
        p = readings[i]["power"]
        ts = readings[i]["ts"]

        if p >= active_thresh:
            if not is_running:
                is_running = True
                cycle_count += 1
                current_run_start = ts
        else:
            if is_running:
                is_running = False
                if current_run_start:
                    duration = (ts - current_run_start).total_seconds()
                    run_durations.append(duration)
                    total_running_seconds += duration
                current_run_start = None

    if is_running and current_run_start:
        duration = (readings[-1]["ts"] - current_run_start).total_seconds()
        run_durations.append(duration)
        total_running_seconds += duration

    total_window_seconds = duration_hours * 3600.0
    runtime_mins = total_running_seconds / 60.0
    duty_cycle_pct = min(100.0, (total_running_seconds / total_window_seconds) * 100.0)
    avg_cycle_mins = (sum(run_durations) / len(run_durations) / 60.0) if run_durations else 0.0
    cycles_per_hour = cycle_count / max(1.0, duration_hours)

    anomalies = []

    # 1. Check Short-Cycling Anomaly (> 10 cycles/hour and avg cycle < 2.5 mins)
    if cycles_per_hour >= 10.0 and avg_cycle_mins < 2.5 and cycle_count >= 12:
        anomalies.append({
            "type": "SHORT_CYCLING",
            "severity": "HIGH",
            "title": "Compressor Short-Cycling Detected",
            "description": f"Equipment cycled {cycle_count} times in {duration_hours:.1f}h (avg run: {avg_cycle_mins:.1f} mins).",
            "cause": "Thermostat jitter, clogged condenser coils, or low refrigerant pressure.",
            "recommendation": "Inspect back ventilation grill and clean condenser coils immediately."
        })

    # 2. Check Severe Continuous 100% Run (> 95% duty cycle over > 6 hours with no cycling)
    if duty_cycle_pct >= 95.0 and duration_hours >= 6.0 and cycle_count <= 1:
        anomalies.append({
            "type": "CONTINUOUS_RUN",
            "severity": "CRITICAL",
            "title": "Continuous Compressor Duty Cycle",
            "description": f"Compressor ran {duty_cycle_pct:.1f}% of the time without resting.",
            "cause": "Door left unsealed/open, damaged gasket, or severe refrigerant loss.",
            "recommendation": "Check door magnetic gasket seal and ensure door is latching shut firmly."
        })

    return {
        "status": "ANOMALY" if anomalies else "HEALTHY",
        "cycle_count": cycle_count,
        "cycles_per_hour": round(cycles_per_hour, 1),
        "runtime_mins": round(runtime_mins, 1),
        "duty_cycle_pct": round(duty_cycle_pct, 1),
        "avg_cycle_mins": round(avg_cycle_mins, 1),
        "peak_power_watts": round(peak_power, 1),
        "anomalies": anomalies
    }


def audit_compressors_health(db: Session, lookback_hours: float = 6.0) -> List[Dict[str, Any]]:
    """
    Audits all active plug sensors across Ground Up fridges and freezers.
    Dispatches WhatsApp alert if a new anomaly is detected.
    """
    plug_sensors = db.query(Sensor).filter(
        Sensor.type == "plug",
        Sensor.active == True,
        Sensor.device_id != None
    ).all()

    now_utc = datetime.utcnow()
    since_ts = now_utc - timedelta(hours=lookback_hours)

    reports = []

    for s in plug_sensors:
        try:
            # Query plug telemetry
            rows = db.execute(text("""
                SELECT apower, voltage, timestamp
                FROM monitoring.plug_telemetry
                WHERE device_id = :did AND timestamp >= :since
                ORDER BY timestamp ASC
            """), {"did": s.device_id, "since": since_ts}).fetchall()

            readings = [{"power": float(r[0] or 0.0), "voltage": float(r[1] or 0.0), "ts": r[2]} for r in rows]
            analysis = analyze_compressor_power_stream(readings, duration_hours=lookback_hours)

            room = db.query(Room).filter(Room.id == s.room_id).first()
            room_name = room.name if room else s.name

            report_item = {
                "sensor_id": str(s.id),
                "sensor_name": s.name,
                "room_name": room_name,
                "device_id": s.device_id,
                **analysis
            }
            reports.append(report_item)

            # If anomalies found, check cooldown and send WhatsApp alert
            if analysis["anomalies"]:
                for anom in analysis["anomalies"]:
                    setting_key = f"last_compressor_alert_{s.device_id}_{anom['type']}"
                    last_alert_setting = db.query(Setting).filter(Setting.key == setting_key).first()
                    
                    should_alert = True
                    if last_alert_setting and last_alert_setting.value:
                        try:
                            last_ts = datetime.fromisoformat(last_alert_setting.value)
                            # 4 hours cooldown between compressor anomaly alerts
                            if (now_utc - last_ts).total_seconds() < 14400:
                                should_alert = False
                        except Exception:
                            pass

                    if should_alert:
                        logger.warning(f"🚨 COMPRESSOR ANOMALY on {s.name}: {anom['title']} - {anom['description']}")
                        alert_msg = (
                            f"⚠️ *COMPRESSOR HEALTH ALERT: {anom['title']}*\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"📍 *Equipment:* {s.name} ({room_name})\n"
                            f"📊 *Duty Cycle:* {analysis['duty_cycle_pct']}% (Avg Run: {analysis['avg_cycle_mins']}m, Cycles: {analysis['cycle_count']})\n"
                            f"⚡ *Peak Power:* {analysis['peak_power_watts']}W\n"
                            f"🔍 *Diagnostic:* {anom['description']}\n"
                            f"💡 *Probable Cause:* {anom['cause']}\n"
                            f"🛠️ *Action:* {anom['recommendation']}"
                        )

                        send_monitoring_alert(
                            sensor_name=s.name,
                            alert_type=f"Compressor {anom['type'].replace('_', ' ').title()}",
                            current_value=f"{analysis['duty_cycle_pct']}% Duty Cycle",
                            normal_range="30% - 60%",
                            duration=f"{lookback_hours:.0f}h window",
                            priority=anom["severity"],
                            alert_id=f"comp_{s.device_id}_{int(now_utc.timestamp())}"
                        )

                        # Record alert setting timestamp
                        if not last_alert_setting:
                            last_alert_setting = Setting(key=setting_key, value=now_utc.isoformat(), description="Last compressor health alert timestamp")
                            db.add(last_alert_setting)
                        else:
                            last_alert_setting.value = now_utc.isoformat()
                        db.commit()

        except Exception as e:
            logger.error(f"Error auditing compressor {s.name}: {e}", exc_info=True)

    return reports
