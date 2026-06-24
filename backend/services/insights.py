"""Insights and Daily Reports service using Gemini API."""
import os
import time
import httpx
import logging
import json
from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import func
from collections import defaultdict

from backend.models.room import Room
from backend.models.sensor import Sensor
from backend.models.alert import Alert
from backend.models.device_telemetry import DeviceTelemetry
from backend.models.plug_telemetry import PlugTelemetry
from backend.models.user import User
from backend.services.email import send_html_email

logger = logging.getLogger(__name__)

def calculate_out_of_bounds_duration(logs: list, min_val: float, max_val: float):
    """
    Calculate the duration (in minutes) where temperature was below min_val or above max_val.
    Assumes logs are sorted ascending by timestamp.
    """
    below_mins = 0.0
    above_mins = 0.0
    for i in range(1, len(logs)):
        dt = (logs[i].timestamp - logs[i-1].timestamp).total_seconds() / 60.0
        if dt > 15.0:  # Ignore gaps larger than 15 minutes
            continue
            
        val = float(logs[i].temperature + logs[i-1].temperature) / 2.0
        if min_val is not None and val < float(min_val):
            below_mins += dt
        if max_val is not None and val > float(max_val):
            above_mins += dt
            
    return round(below_mins, 1), round(above_mins, 1)

def query_24h_room_metrics(db: Session, room: Room, cutoff: datetime, sensors: list):
    """Compile 24-hour temperature, humidity, and plug metrics for a room."""
    # Find sensors linked to the room
    temp_sensor = next((s for s in sensors if s.type == "temperature" and s.active), None)
    hum_sensor = next((s for s in sensors if s.type == "humidity" and s.active), None)
    
    device_id = temp_sensor.device_id if temp_sensor else None
    threshold = float(temp_sensor.tapo_running_threshold) if (temp_sensor and temp_sensor.tapo_running_threshold is not None) else 80.0
    
    temp_min_th = float(temp_sensor.min_threshold) if (temp_sensor and temp_sensor.min_threshold is not None) else None
    temp_max_th = float(temp_sensor.max_threshold) if (temp_sensor and temp_sensor.max_threshold is not None) else None

    # 1. Temp & Humidity metrics
    t_avg, t_min, t_max = None, None, None
    h_avg, h_min, h_max = None, None, None
    below_mins, above_mins = 0.0, 0.0
    
    if device_id:
        t_logs = db.query(DeviceTelemetry).filter(
            DeviceTelemetry.device_id == device_id,
            DeviceTelemetry.timestamp >= cutoff
        ).order_by(DeviceTelemetry.timestamp.asc()).all()
        
        if t_logs:
            temp_vals = [float(log.temperature) for log in t_logs if log.temperature is not None]
            hum_vals = [float(log.humidity) for log in t_logs if log.humidity is not None]
            
            if temp_vals:
                t_avg = round(sum(temp_vals) / len(temp_vals), 1)
                t_min = round(min(temp_vals), 1)
                t_max = round(max(temp_vals), 1)
                
            if hum_vals:
                h_avg = round(sum(hum_vals) / len(hum_vals), 1)
                h_min = round(min(hum_vals), 1)
                h_max = round(max(hum_vals), 1)
                
            below_mins, above_mins = calculate_out_of_bounds_duration(t_logs, temp_min_th, temp_max_th)

    below_hours = round(below_mins / 60.0, 1)
    if below_hours == 0.0 and below_mins > 0:
        below_hours = 0.1
        
    above_hours = round(above_mins / 60.0, 1)
    if above_hours == 0.0 and above_mins > 0:
        above_hours = 0.1

    # 2. Plug / compressor metrics
    p_avg, p_min, p_max = None, None, None
    energy_kwh = None
    runtime_hours = None
    starts_count = None
    
    has_plug = temp_sensor is not None and temp_sensor.tapo_ip is not None and len(str(temp_sensor.tapo_ip).strip()) > 0
    
    if has_plug and device_id:
        p_logs = db.query(PlugTelemetry).filter(
            PlugTelemetry.device_id == device_id,
            PlugTelemetry.timestamp >= cutoff
        ).order_by(PlugTelemetry.timestamp.asc()).all()
        
        if p_logs:
            energy_kwh = 0.0
            runtime_hours = 0.0
            starts_count = 0
            
            power_vals = [float(log.apower) for log in p_logs if log.apower is not None]
            energy_vals = [float(log.today_energy) for log in p_logs if log.today_energy is not None]
            
            if power_vals:
                p_avg = round(sum(power_vals) / len(power_vals), 1)
                p_min = round(min(power_vals), 1)
                p_max = round(max(power_vals), 1)
                
            if energy_vals:
                energy_kwh = round(max(energy_vals) / 1000.0, 3)
                
            was_running = False
            for i, log in enumerate(p_logs):
                curr_running = float(log.apower) >= threshold
                if curr_running and not was_running:
                    starts_count += 1
                was_running = curr_running
                
                if i > 0 and curr_running:
                    delta = (log.timestamp - p_logs[i-1].timestamp).total_seconds() / 3600.0
                    if delta < 0.25:
                        runtime_hours += delta
                        
    return {
        "room_name": room.name,
        "temp_sensor_id": str(temp_sensor.id) if temp_sensor else None,
        "temp_min_threshold": temp_min_th,
        "temp_max_threshold": temp_max_th,
        "t_avg": t_avg,
        "t_min": t_min,
        "t_max": t_max,
        "below_min_hours": below_hours,
        "above_max_hours": above_hours,
        "h_avg": h_avg,
        "h_min": h_min,
        "h_max": h_max,
        "has_plug": has_plug,
        "p_avg": p_avg,
        "p_min": p_min,
        "p_max": p_max,
        "runtime_hours": round(runtime_hours, 2) if runtime_hours is not None else None,
        "starts_count": starts_count,
        "energy_kwh": energy_kwh
    }

def fetch_7d_baselines(db: Session, room: Room, sensors: list):
    """Calculate average daily baselines for a room over the last 7 days."""
    temp_sensor = next((s for s in sensors if s.type == "temperature" and s.active), None)
    device_id = temp_sensor.device_id if temp_sensor else None
    threshold = float(temp_sensor.tapo_running_threshold) if (temp_sensor and temp_sensor.tapo_running_threshold is not None) else 80.0
    has_plug = temp_sensor is not None and temp_sensor.tapo_ip is not None and len(str(temp_sensor.tapo_ip).strip()) > 0

    if not device_id:
        return {
            "t_avg": None,
            "has_plug": has_plug,
            "runtime_hours": 0.0 if has_plug else None,
            "starts_count": 0.0 if has_plug else None,
            "energy_kwh": 0.0 if has_plug else None
        }

    cutoff_7d = datetime.utcnow() - timedelta(days=7)
    
    # 1. Temp baseline
    t_avg_val = db.query(func.avg(DeviceTelemetry.temperature)).filter(
        DeviceTelemetry.device_id == device_id,
        DeviceTelemetry.timestamp >= cutoff_7d
    ).scalar()
    t_avg = round(float(t_avg_val), 1) if t_avg_val is not None else None

    # 2. Plug baseline
    if not has_plug:
        return {
            "t_avg": t_avg,
            "has_plug": False,
            "runtime_hours": None,
            "starts_count": None,
            "energy_kwh": None
        }

    p_logs = db.query(PlugTelemetry).filter(
        PlugTelemetry.device_id == device_id,
        PlugTelemetry.timestamp >= cutoff_7d
    ).order_by(PlugTelemetry.timestamp.asc()).all()

    if not p_logs:
        return {
            "t_avg": t_avg,
            "has_plug": True,
            "runtime_hours": None,
            "starts_count": None,
            "energy_kwh": None
        }

    daily_logs = defaultdict(list)
    for log in p_logs:
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
    
    return {
        "t_avg": t_avg,
        "has_plug": True,
        "runtime_hours": round(sum(daily_runtimes) / num_days, 2),
        "starts_count": round(sum(daily_starts) / num_days, 1),
        "energy_kwh": round(sum(daily_energies) / num_days, 3)
    }

async def call_gemini_diagnose(telemetry_data: list) -> dict:
    """Send structured telemetry data to the Gemini API to get diagnostic insights."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.warning("No GEMINI_API_KEY configured. Falling back to rule-based insights.")
        return generate_rule_based_diagnoses(telemetry_data)

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    
    prompt = f"""
    You are an expert thermal dynamics and refrigeration maintenance engineer for a food/fermentation factory (Ground Up, Pune, India). Analyze the 24h operational telemetry below, comparing against 7-day baselines.
    
    CRITICAL CONTEXT:
    - Each entry has a "room_type" field: "fridge", "freezer", or "room".
    - Fridges and freezers run compressors near-continuously (20-24 hours/day is NORMAL). Do NOT flag high absolute runtime as a problem. Only flag significant CHANGES vs the 7-day baseline.
    - "room" type entries have AC units. ACs can also run long hours during hot days. Again, focus on CHANGE vs baseline, not absolute runtime.
    - The smart plug is always powered ON. The "apower" (watts) field shows compressor state: high power = compressor running, low power = compressor standby. The plug itself never turns off.
    - Standby power varies by device type: fridges draw ~100-130W even in standby (fans/electronics), AC units draw ~30W in standby.
    - The "tapo_running_threshold" determines the boundary between standby and running. This is calibrated per device.
    - For devices WITHOUT a plug (has_plug = false), diagnose on temperature only.
    
    DIAGNOSTIC APPROACH - Combine temperature + power signals:
    - Temp rising above max + low runtime = Equipment not cooling (power issue, turned off)
    - Temp rising above max + high runtime = Compressor running but failing (gas leak, dirty coils, door open)
    - Temp normal + runtime significantly above baseline = Early inefficiency (system overworking)
    - Temp normal + starts significantly above baseline = Short-cycling
    - Temp falling below min = Over-cooling / thermostat issue
    - Temp normal + runtime normal = Healthy
    
    Telemetry Data:
    {json.dumps(telemetry_data, indent=2)}

    Format your output strictly as a JSON object matching this schema:
    {{
      "overall_status": "healthy" | "warning" | "critical",
      "whatsapp_message": "A short, friendly 2-3 sentence overview for the factory manager.",
      "diagnoses": [
        {{
          "room_name": "Name of the room",
          "status": "healthy" | "warning" | "critical",
          "analysis": "A detailed engineering observation correlating temperature behavior with compressor runtime, cycle count, and energy draw. Reference both temperature and power data together.",
          "action_items": [
            "Specific recommendations (e.g. check gaskets, clean condenser coils, inspect thermostat settings, check power connection)"
          ]
        }}
      ]
    }}
    """
    
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "response_mime_type": "application/json"
        }
    }
    
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=payload, timeout=20.0)
            if res.status_code == 200:
                result = res.json()
                text = result["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(text)
            else:
                logger.error(f"Gemini API returned status {res.status_code}: {res.text}")
    except Exception as e:
        logger.error(f"Failed to communicate with Gemini API: {e}", exc_info=True)
        
    return generate_rule_based_diagnoses(telemetry_data)

def _type_label(room_type: str) -> str:
    """Return a human-friendly label for a room type."""
    return {"fridge": "Fridge", "freezer": "Freezer", "room": "Room"}.get(room_type, "Unit")

def generate_rule_based_diagnoses(telemetry_data: list) -> dict:
    """Fail-safe fallback generator using combined temp+power signal matrix."""
    overall_status = "healthy"
    diagnoses = []
    
    for r in telemetry_data:
        metrics = r["last_24h"]
        baseline = r["baseline_7d"]
        room_type = r.get("room_type", "room")
        label = _type_label(room_type)
        has_plug = metrics.get("has_plug", False)
        
        status = "healthy"
        analysis = f"{label} parameters are operating within normal tolerances."
        action_items = ["No actions required."]
        
        temp_high = metrics.get("above_max_hours") is not None and metrics["above_max_hours"] > 0.25
        temp_low = metrics.get("below_min_hours") is not None and metrics["below_min_hours"] > 0.25
        
        if has_plug and metrics.get("runtime_hours") is not None:
            # --- COMBINED SIGNAL MATRIX (temp + power) ---
            runtime_today = metrics["runtime_hours"]
            runtime_baseline = baseline.get("runtime_hours")
            starts_today = metrics.get("starts_count") or 0
            starts_baseline = baseline.get("starts_count") or 0
            
            if temp_high and runtime_today < 0.5:
                # Temp rising + power low = equipment not running
                status = "critical"
                analysis = (f"{label} temperature exceeded maximum threshold for {metrics['above_max_hours']}h today, "
                           f"but the compressor ran only {runtime_today}h. The cooling equipment appears to not be running. "
                           f"Average power draw was {metrics.get('p_avg', 0)}W.")
                action_items = ["Check if the cooling unit is powered on.", "Inspect circuit breakers and power cables.",
                               "Check thermostat/controller board.", "Verify the equipment is not switched off."]
            elif temp_high and runtime_today >= 0.5:
                # Temp rising + power high = compressor running but failing to cool
                status = "critical"
                analysis = (f"{label} temperature exceeded maximum threshold for {metrics['above_max_hours']}h today "
                           f"while the compressor ran for {runtime_today}h ({starts_today} cycles). "
                           f"The compressor is running but failing to maintain target temperature.")
                action_items = ["Inspect door seals and gaskets.", "Verify the door was not left open.",
                               "Check refrigerant gas levels.", "Clean condenser and evaporator coils.",
                               "Ensure product load does not block airflow."]
            elif temp_low:
                # Temperature too low
                status = "warning"
                analysis = (f"{label} temperature fell below minimum threshold for {metrics['below_min_hours']}h today. "
                           f"Compressor ran {runtime_today}h with {starts_today} cycles. "
                           f"The unit may be over-cooling, which can damage products.")
                action_items = ["Calibrate thermostat/controller settings.", "Check temperature sensor placement.",
                               "Ensure auto-defrost cycle is functioning."]
            elif (runtime_baseline is not None and runtime_baseline > 0.5 and
                  runtime_today > 1.3 * runtime_baseline):
                # Temp normal + runtime significantly above baseline = early inefficiency
                status = "warning"
                pct_increase = round(100 * (runtime_today - runtime_baseline) / runtime_baseline)
                analysis = (f"Temperature is stable, but compressor runtime today ({runtime_today}h) is "
                           f"{pct_increase}% higher than the 7-day average ({runtime_baseline}h). "
                           f"The system is working harder to maintain the same temperature.")
                action_items = ["Clean condenser coils.", "Check door alignment and gaskets.",
                               "Ensure ambient ventilation is adequate.", "Check for product overloading."]
            elif (starts_baseline is not None and starts_baseline > 2 and
                  starts_today > 1.5 * starts_baseline):
                # Temp normal + starts spiking = short-cycling
                status = "warning"
                analysis = (f"Temperature is stable, but compressor cycle count today ({starts_today}) is "
                           f"significantly higher than the 7-day average ({starts_baseline}). This indicates short-cycling.")
                action_items = ["Check thermostat temperature differential settings.",
                               "Clean evaporator/condenser filters.", "Inspect thermostat sensor placement."]
        else:
            # --- TEMPERATURE-ONLY DIAGNOSIS (no plug data / plug offline) ---
            plug_reason = "Smart plug is offline" if has_plug else "No smart plug is installed"
            if temp_high:
                status = "critical"
                analysis = (f"{label} temperature exceeded maximum threshold for {metrics['above_max_hours']}h today. "
                           f"{plug_reason}, so power status cannot be verified.")
                action_items = ["Verify the cooling unit is powered on and functioning.",
                               "Check door seals and gaskets.", "Verify the door was not left open."]
            elif temp_low:
                status = "warning"
                analysis = (f"{label} temperature fell below minimum threshold for {metrics['below_min_hours']}h today. "
                           f"The unit may be over-cooling.")
                action_items = ["Calibrate thermostat controller settings.",
                               "Ensure auto-defrost cycle is functioning."]
            
        if status == "critical":
            overall_status = "critical"
        elif status == "warning" and overall_status != "critical":
            overall_status = "warning"
            
        diagnoses.append({
            "room_name": r["room_name"],
            "status": status,
            "analysis": analysis,
            "action_items": action_items
        })
        
    status_emoji = "🟢" if overall_status == "healthy" else ("🟡" if overall_status == "warning" else "🔴")
    msg = f"{status_emoji} Daily Equipment Report: "
    if overall_status == "healthy":
        msg += "All factory equipment (fridges, freezers, and AC units) are operating normally."
    elif overall_status == "warning":
        msg += "Minor inefficiencies detected in some equipment. Review the maintenance checklist."
    else:
        msg += "CRITICAL: Temperature violations or equipment failures detected. Immediate action required!"
        
    return {
        "overall_status": overall_status,
        "whatsapp_message": msg,
        "diagnoses": diagnoses
    }

def build_report_html(report_date: str, overall_status: str, summary_msg: str, telemetry_data: list, insights: dict) -> str:
    """Build a premium, responsive HTML email template with inline styles."""
    status_colors = {
        "healthy": {"bg": "#E6F4EA", "border": "#34A853", "text": "#137333", "badge": "🟢 HEALTHY"},
        "warning": {"bg": "#FEF7E0", "border": "#FBBC04", "text": "#B06000", "badge": "⚠️ WARNING"},
        "critical": {"bg": "#FCE8E6", "border": "#EA4335", "text": "#C5221F", "badge": "🚨 CRITICAL"}
    }
    
    current_status = status_colors.get(overall_status, status_colors["healthy"])
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>Daily Cold Storage & Equipment Insights Report</title>
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #F8FAFC; margin: 0; padding: 12px 8px; color: #1E293B;">
      <div style="max-width: 650px; margin: 0 auto; background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
        
        <!-- Header Banner -->
        <div style="background: linear-gradient(135deg, #0F172A 0%, #1E293B 100%); padding: 30px 16px; text-align: center; color: #FFFFFF;">
          <h1 style="margin: 0; font-size: 22px; font-weight: 800; letter-spacing: 0.5px;">❄️ Ground Up Equipment Insights</h1>
          <p style="margin: 6px 0 0 0; font-size: 13px; color: #94A3B8; font-weight: 500;">Daily Cold Storage & AC Diagnostics — {report_date}</p>
        </div>

        <!-- Overall Status Alert -->
        <div style="margin: 16px; padding: 16px; background-color: {current_status['bg']}; border-left: 5px solid {current_status['border']}; border-radius: 8px;">
          <div style="font-weight: 800; font-size: 14px; color: {current_status['text']}; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px;">
            {current_status['badge']}
          </div>
          <p style="margin: 0; font-size: 13px; color: #334155; line-height: 1.4; font-weight: 500;">{summary_msg}</p>
        </div>

        <!-- Table: Telemetry Stats -->
        <div style="margin: 0 16px 20px 16px;">
          <h2 style="font-size: 12px; font-weight: 800; color: #64748B; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px;">📊 Telemetry Performance Comparison</h2>
          <div style="border: 1px solid #E2E8F0; border-radius: 12px; overflow: hidden; background: #F8FAFC; overflow-x: auto; -webkit-overflow-scrolling: touch;">
            <table style="width: 100%; table-layout: fixed; border-collapse: collapse; text-align: left; font-size: 11px;">
              <thead>
                <tr style="background: #E2E8F0; color: #334155; font-weight: 800; font-size: 9px; text-transform: uppercase; letter-spacing: 0.5px;">
                  <th style="padding: 10px 8px; width: 25%; word-wrap: break-word;">Equipment</th>
                  <th style="padding: 10px 8px; width: 20%; text-align: center; word-wrap: break-word;">Temp (24h)</th>
                  <th style="padding: 10px 8px; width: 21%; text-align: center; word-wrap: break-word;">Out of Bounds</th>
                  <th style="padding: 10px 8px; width: 19%; text-align: center; word-wrap: break-word;">Use Time (24h vs 7d)</th>
                  <th style="padding: 10px 8px; width: 15%; text-align: center; word-wrap: break-word;">Energy</th>
                </tr>
              </thead>
              <tbody>
    """
    
    for r in telemetry_data:
        m = r["last_24h"]
        b = r["baseline_7d"]
        room_type = r.get("room_type", "room")
        type_icon = {"fridge": "🧊", "freezer": "🥶", "room": "🌡️"}.get(room_type, "📦")
        type_label = _type_label(room_type)
        
        temp_range_str = f"{m['t_min'] or '--'} to {m['t_max'] or '--'} °C"
        
        # Out-of-bounds metrics
        bounds_parts = []
        if m.get("below_min_hours") is not None and m["below_min_hours"] > 0:
            bounds_parts.append(f"❄️ {m['below_min_hours']}h low")
        if m.get("above_max_hours") is not None and m["above_max_hours"] > 0:
            bounds_parts.append(f"🔥 {m['above_max_hours']}h high")
            
        bounds_str = ", ".join(bounds_parts) if bounds_parts else "🟢 Normal"
        bounds_color = "#EA4335" if bounds_parts else "#34A853"
        
        has_plug = m.get("has_plug", False)
        has_plug_logs = has_plug and m.get("runtime_hours") is not None
        if has_plug_logs:
            avg_val = b.get('runtime_hours')
            avg_str = f"avg: {avg_val:.1f}h" if avg_val is not None else "avg: —"
            runtime_str = f"""
            <div style="font-weight: 700;">{m['runtime_hours']:.1f}h</div>
            <div style="font-size: 10px; color: #64748B;">({avg_str})</div>
            """
            energy_val = m.get('energy_kwh')
            energy_val_str = f"{energy_val:.3f} kWh" if energy_val is not None else "0.000 kWh"
            energy_str = f"<span style='font-weight: 700; color: #0F172A;'>{energy_val_str}</span>"
        elif has_plug:
            avg_val = b.get('runtime_hours')
            avg_str = f"avg: {avg_val:.1f}h" if avg_val is not None else "avg: —"
            runtime_str = f"""
            <div style="font-weight: 700; color: #64748B;">0.0h <span style="font-size: 9px; color: #EA4335; font-weight: bold;">(Offline)</span></div>
            <div style="font-size: 10px; color: #64748B;">({avg_str})</div>
            """
            energy_str = "<span style='font-weight: 700; color: #64748B;'>0.000 kWh <span style='font-size: 9px; color: #EA4335; font-weight: bold;'>(Offline)</span></span>"
        else:
            runtime_str = "<span style='color: #94A3B8;'>—</span>"
            energy_str = "<span style='color: #94A3B8;'>—</span>"
            
        html += f"""
                <tr style="border-top: 1px solid #E2E8F0; background: #FFFFFF; color: #334155;">
                  <td style="padding: 12px; font-weight: bold; color: #0F172A;">
                    <div>{type_icon} {r['room_name']}</div>
                    <div style="font-size: 9px; color: #94A3B8; font-weight: 600; text-transform: uppercase;">{type_label}</div>
                  </td>
                  <td style="padding: 12px; text-align: center;">
                    <div style="font-weight: 700;">{m['t_avg'] or '--'}°C</div>
                    <div style="font-size: 10px; color: #64748B;">({temp_range_str})</div>
                  </td>
                  <td style="padding: 12px; text-align: center; font-weight: 700; color: {bounds_color};">{bounds_str}</td>
                  <td style="padding: 12px; text-align: center;">
                    {runtime_str}
                  </td>
                  <td style="padding: 12px; text-align: center;">
                    {energy_str}
                  </td>
                </tr>
        """
        
    html += """
              </tbody>
            </table>
          </div>
        </div>

        <!-- Section: AI Diagnostic Findings -->
        <div style="margin: 0 16px 20px 16px;">
          <h2 style="font-size: 12px; font-weight: 800; color: #64748B; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px;">🔧 Maintenance & Diagnostic Findings</h2>
          <div>
    """
    
    has_visible_diagnoses = False
    for diag in insights.get("diagnoses", []):
        diag_status = diag.get("status", "healthy")
        if diag_status == "healthy":
            continue  # Skip healthy units to keep diagnostic report clean and focused on action items
            
        has_visible_diagnoses = True
        diag_colors = status_colors.get(diag_status, status_colors["healthy"])
        
        # Find matching telemetry for plug metrics
        matched_telemetry = next((t for t in telemetry_data if t["room_name"] == diag["room_name"]), None)
        plug_metrics_html = ""
        if matched_telemetry:
            mt = matched_telemetry["last_24h"]
            mb = matched_telemetry["baseline_7d"]
            if mt.get("has_plug") and mt.get("runtime_hours") is not None:
                runtime_today = mt['runtime_hours'] or 0.0
                runtime_avg = mb.get('runtime_hours') or 0.0
                starts_today = mt.get('starts_count') or 0
                starts_avg = mb.get('starts_count') or 0
                energy_today = mt.get('energy_kwh') or 0.0
                plug_metrics_html = f"""
              <div style="background: #F1F5F9; border-radius: 6px; padding: 8px 10px; margin-bottom: 12px; font-size: 11px; color: #475569; line-height: 1.5;">
                <div style="font-weight: 800; color: #64748B; font-size: 9px; text-transform: uppercase; margin-bottom: 4px;">⚡ Plug Metrics (24h)</div>
                <div>Runtime: <b>{runtime_today}h</b> (avg {runtime_avg}h) &nbsp;|&nbsp; Cycles: <b>{starts_today}</b> (avg {starts_avg}) &nbsp;|&nbsp; Energy: <b>{energy_today:.3f} kWh</b></div>
              </div>
                """
        
        html += f"""
            <div style="border: 1px solid #E2E8F0; border-radius: 12px; padding: 16px; background-color: #FFFFFF; margin-bottom: 14px;">
              <table style="width: 100%; border-collapse: collapse; margin-bottom: 10px; border-bottom: 1px solid #F1F5F9; padding-bottom: 6px;">
                <tr>
                  <td style="font-size: 14px; font-weight: 800; color: #0F172A; padding: 0 0 6px 0;">{diag['room_name']}</td>
                  <td style="text-align: right; padding: 0 0 6px 0;">
                    <span style="font-size: 9px; font-weight: 800; background-color: {diag_colors['bg']}; color: {diag_colors['text']}; padding: 2px 8px; border-radius: 4px; text-transform: uppercase; border: 1px solid {diag_colors['border']}33; display: inline-block;">
                      {diag_status.upper()}
                    </span>
                  </td>
                </tr>
              </table>
              <p style="margin: 0 0 12px 0; font-size: 12.5px; line-height: 1.4; color: #334155;">{diag['analysis']}</p>
              {plug_metrics_html}
              <div style="font-size: 11px; font-weight: 800; color: #64748B; margin-bottom: 6px; text-transform: uppercase;">🔧 Recommended Actions:</div>
              <ul style="margin: 0; padding-left: 20px; font-size: 12px; color: #475569; line-height: 1.4;">
        """
        for item in diag.get("action_items", []):
            html += f"<li style='margin-bottom: 4px;'>{item}</li>"
            
        html += """
              </ul>
            </div>
        """
        
    if not has_visible_diagnoses:
        html += """
            <div style="border: 1px solid #E2E8F0; border-radius: 12px; padding: 16px; background-color: #FFFFFF; margin-bottom: 14px; text-align: center; color: #64748B; font-size: 13px;">
              🟢 All equipment (fridges, freezers, and AC units) are functioning within normal parameters.
            </div>
        """
        
    html += f"""
          </div>
        </div>

        <!-- Footer -->
        <div style="background-color: #F8FAFC; border-top: 1px solid #E2E8F0; padding: 20px; text-align: center; font-size: 11px; color: #94A3B8; line-height: 1.5;">
          <p style="margin: 0 0 4px 0;">This email is auto-generated by the Ground Up Monitoring System.</p>
          <p style="margin: 0;">To update report recipient email configurations, edit the GAE dashboard environment variables.</p>
        </div>

      </div>
    </body>
    </html>
    """
    return html

async def generate_report_html(db: Session) -> tuple[str, str]:
    """Compile stats for all cold rooms, perform AI diagnostics, and build HTML body."""
    report_date = (datetime.utcnow() - timedelta(days=1)).strftime("%B %d, %Y")
    
    # 1. Fetch active rooms
    rooms = db.query(Room).filter(Room.active == True).all()
    if not rooms:
        logger.warning("No active rooms found in database.")
        return "<html><body><h3>No active rooms found in database.</h3></body></html>", "healthy"
        
    # Filter rooms that have at least one temperature sensor configured
    monitored_rooms = []
    for r in rooms:
        room_sensors = db.query(Sensor).filter(Sensor.room_id == r.id, Sensor.active == True).all()
        has_temp = any(s.type == "temperature" for s in room_sensors)
        if has_temp:
            monitored_rooms.append((r, room_sensors))
            
    if not monitored_rooms:
        logger.info("No rooms have active temperature sensors.")
        return "<html><body><h3>No rooms have active temperature sensors configured.</h3></body></html>", "healthy"

    # 2. Gather 24h metrics and 7d baselines
    cutoff_24h = datetime.utcnow() - timedelta(hours=24)
    telemetry_data = []
    
    for room, room_sensors in monitored_rooms:
        last_24h = query_24h_room_metrics(db, room, cutoff_24h, room_sensors)
        baselines = fetch_7d_baselines(db, room, room_sensors)
        
        telemetry_data.append({
            "room_name": room.name,
            "room_type": room.type,
            "last_24h": last_24h,
            "baseline_7d": baselines
        })

    # 3. Call Gemini to get diagnostic insights
    insights = await call_gemini_diagnose(telemetry_data)
    
    overall_status = insights.get("overall_status", "healthy")
    whatsapp_msg = insights.get("whatsapp_message", "Report compiled successfully.")

    # 4. Generate HTML email body
    html_body = build_report_html(
        report_date=report_date,
        overall_status=overall_status,
        summary_msg=whatsapp_msg,
        telemetry_data=telemetry_data,
        insights=insights
    )
    return html_body, overall_status

async def generate_daily_report(db: Session):
    """Compile stats for all cold rooms, perform AI diagnostics, and send the email report."""
    logger.info("Starting daily temperature & energy report generation...")
    report_date = (datetime.utcnow() - timedelta(days=1)).strftime("%B %d, %Y")
    
    html_body, overall_status = await generate_report_html(db)
    if "No rooms have active temperature sensors" in html_body or "No active rooms found" in html_body:
        return False
        
    # 5. Compile recipient lists
    recipients = []
    
    # Add from DB (active users who opted in)
    db_users = db.query(User).filter(User.active == True, User.receive_reports == True).all()
    for u in db_users:
        if u.email and u.email.strip():
            email_val = u.email.strip()
            if not email_val.lower().endswith("@groundup.app"):
                recipients.append(email_val)
            
    # Add from ENV (fallback)
    env_recipients = os.getenv("REPORT_RECIPIENT")
    if env_recipients:
        for r in env_recipients.split(","):
            if r.strip() and r.strip() not in recipients:
                recipients.append(r.strip())
                
    if not recipients:
        logger.warning("No recipient emails found in database or REPORT_RECIPIENT env. Aborting send.")
        return False

    # 6. Send the HTML report email
    subject = f"[Cold Storage Report] {report_date} - Status: {overall_status.upper()}"
    success = send_html_email(subject=subject, html_body=html_body, recipients=recipients)
    
    if success:
        logger.info("Daily cold storage insights email sent successfully.")
    else:
        logger.error("Failed to send daily cold storage insights email.")
        
    return success

