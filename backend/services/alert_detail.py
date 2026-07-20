import os
import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from backend.models.alert import Alert
from backend.models.sensor import Sensor
from backend.models.room import Room

logger = logging.getLogger(__name__)

def generate_alert_detail_html(alert_id_str: str, db: Session) -> str:
    """Generate a responsive HTML view for a specific alert detail link."""
    alert = None
    
    # Try fetching by exact alert UUID
    try:
        import uuid
        alert_uuid = uuid.UUID(alert_id_str)
        alert = db.query(Alert).filter(Alert.id == alert_uuid).first()
    except (ValueError, AttributeError, TypeError):
        pass

    # Fallback: if alert_id_str is 'active', 'latest', 'all_offline', or invalid, get latest unresolved alert
    if not alert:
        alert = db.query(Alert).filter(Alert.resolved == False).order_by(Alert.created_at.desc()).first()
    
    # Second fallback: get most recent alert overall
    if not alert:
        alert = db.query(Alert).order_by(Alert.created_at.desc()).first()

    if not alert:
        return """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Alert Not Found - Ground Up</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Inter', sans-serif; background: #f8fafc; color: #1e293b; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; }
        .card { background: white; padding: 30px; border-radius: 20px; box-shadow: 0 10px 25px rgba(0,0,0,0.05); text-align: center; max-width: 400px; width: 100%; border: 1px solid #e2e8f0; }
        h2 { margin-top: 0; color: #0f172a; }
        .btn { display: inline-block; background: #2563eb; color: white; text-decoration: none; padding: 12px 24px; border-radius: 12px; font-weight: 700; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="card">
        <div style="font-size: 48px; margin-bottom: 10px;">✅</div>
        <h2>No Active Alerts</h2>
        <p style="color: #64748b; font-size: 14px;">All sensors and cold room equipment are operating within normal parameters.</p>
        <a href="/" class="btn">Return to Dashboard</a>
    </div>
</body>
</html>"""

    sensor = db.query(Sensor).filter(Sensor.id == alert.sensor_id).first()
    room = db.query(Room).filter(Room.id == sensor.room_id).first() if (sensor and sensor.room_id) else None

    sensor_name = sensor.name if sensor else "Unknown Sensor"
    room_name = room.name if room else "Cold Room Storage"
    device_id = sensor.device_id if sensor else "N/A"
    
    # Calculate IST time
    ist_offset = timedelta(hours=5, minutes=30)
    created_ist = (alert.created_at + ist_offset).strftime("%b %d, %Y at %I:%M %p IST") if alert.created_at else "Recently"
    
    # Format min/max bounds
    min_th = float(sensor.min_threshold) if (sensor and sensor.min_threshold is not None) else None
    max_th = float(sensor.max_threshold) if (sensor and sensor.max_threshold is not None) else None
    
    if min_th is not None and max_th is not None:
        range_str = f"{min_th}°C to {max_th}°C"
    elif max_th is not None:
        range_str = f"Max {max_th}°C"
    elif min_th is not None:
        range_str = f"Min {min_th}°C"
    else:
        range_str = "Standard Bounds"

    is_resolved = alert.resolved
    alert_val = f"{alert.value:.1f}°C" if alert.value is not None else "N/A"
    
    # Priority
    priority = "HIGH"
    if "critical" in (alert.message or "").lower():
        priority = "CRITICAL"
    elif "offline" in (alert.message or "").lower():
        priority = "HIGH"
    elif min_th is not None and alert.value and alert.value < min_th:
        priority = "MEDIUM"

    badge_bg = "#EF4444" if not is_resolved else "#10B981"
    status_text = "🚨 ACTIVE ALERT" if not is_resolved else "✅ RESOLVED"
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Alert Detail - {room_name}</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-color: #F8FAFC;
            --card-bg: #FFFFFF;
            --text-main: #0F172A;
            --text-sub: #64748B;
            --border-color: #E2E8F0;
            --accent-blue: #2563EB;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'Inter', sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            padding: 20px 16px;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
        }}
        .container {{
            width: 100%;
            max-width: 440px;
            background: var(--card-bg);
            border-radius: 24px;
            border: 1px solid var(--border-color);
            box-shadow: 0 20px 40px rgba(15, 23, 42, 0.08);
            overflow: hidden;
        }}
        .header {{
            background: #0F172A;
            color: white;
            padding: 20px;
            text-align: center;
        }}
        .header-title {{
            font-size: 14px;
            font-weight: 800;
            letter-spacing: 1px;
            text-transform: uppercase;
            color: #94A3B8;
        }}
        .header-room {{
            font-size: 20px;
            font-weight: 900;
            margin-top: 4px;
            color: #FFFFFF;
        }}
        .status-bar {{
            background: {badge_bg};
            color: white;
            text-align: center;
            padding: 10px;
            font-size: 13px;
            font-weight: 900;
            letter-spacing: 0.5px;
        }}
        .body-content {{
            padding: 24px 20px;
        }}
        .message-box {{
            background: rgba(239, 68, 68, 0.06);
            border: 1px solid rgba(239, 68, 68, 0.2);
            border-radius: 16px;
            padding: 16px;
            margin-bottom: 20px;
        }}
        .message-title {{
            font-size: 11px;
            font-weight: 800;
            color: #DC2626;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 4px;
        }}
        .message-text {{
            font-size: 14px;
            font-weight: 700;
            color: #991B1B;
            line-height: 1.4;
        }}
        .grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            margin-bottom: 24px;
        }}
        .metric-card {{
            background: #F8FAFC;
            border: 1px solid #E2E8F0;
            border-radius: 14px;
            padding: 14px 12px;
            text-align: center;
        }}
        .metric-label {{
            font-size: 10px;
            font-weight: 800;
            color: var(--text-sub);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 4px;
        }}
        .metric-val {{
            font-size: 18px;
            font-weight: 900;
            color: var(--text-main);
        }}
        .info-list {{
            border-top: 1px solid var(--border-color);
            padding-top: 16px;
            margin-bottom: 24px;
        }}
        .info-row {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 0;
            font-size: 13px;
        }}
        .info-label {{
            color: var(--text-sub);
            font-weight: 600;
        }}
        .info-value {{
            color: var(--text-main);
            font-weight: 800;
        }}
        .action-btn {{
            width: 100%;
            padding: 14px;
            border-radius: 14px;
            border: none;
            font-size: 14px;
            font-weight: 800;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            text-decoration: none;
            box-shadow: 0 4px 12px rgba(37, 99, 235, 0.15);
        }}
        .btn-resolve {{
            background: #10B981;
            color: white;
            margin-bottom: 10px;
        }}
        .btn-resolve:hover {{
            background: #059669;
        }}
        .btn-dashboard {{
            background: #2563EB;
            color: white;
        }}
        .btn-dashboard:hover {{
            background: #1D4ED8;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="header-title">🏭 Ground Up Cold Storage</div>
            <div class="header-room">📍 {room_name}</div>
        </div>

        <div id="statusBadge" class="status-bar">
            {status_text}
        </div>

        <div class="body-content">
            <div class="message-box">
                <div class="message-title">Alert Details</div>
                <div class="message-text">{alert.message}</div>
            </div>

            <div class="grid">
                <div class="metric-card">
                    <div class="metric-label">Recorded Value</div>
                    <div class="metric-val" style="color: #DC2626;">{alert_val}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Normal Range</div>
                    <div class="metric-val" style="font-size: 14px; margin-top: 3px;">{range_str}</div>
                </div>
            </div>

            <div class="info-list">
                <div class="info-row">
                    <span class="info-label">Sensor Name</span>
                    <span class="info-value">{sensor_name}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Device ID</span>
                    <span class="info-value">{device_id}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Priority Level</span>
                    <span class="info-value" style="color: #DC2626;">{priority}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Triggered At</span>
                    <span class="info-value" style="font-size: 12px;">{created_ist}</span>
                </div>
            </div>

            <div id="actionContainer">
                {"<button id='resolveBtn' onclick='resolveAlert()' class='action-btn btn-resolve'>✅ Mark Alert as Resolved</button>" if not is_resolved else ""}
                <a href="/" class="action-btn btn-dashboard">📊 Open Live Dashboard</a>
            </div>
        </div>
    </div>

    <script>
        async function resolveAlert() {{
            const btn = document.getElementById('resolveBtn');
            if (btn) {{
                btn.disabled = true;
                btn.textContent = 'Resolving...';
            }}
            try {{
                const res = await fetch('/alerts/{alert.id}/resolve_public', {{ method: 'POST' }});
                if (res.ok) {{
                    document.getElementById('statusBadge').style.background = '#10B981';
                    document.getElementById('statusBadge').textContent = '✅ RESOLVED';
                    if (btn) btn.remove();
                }} else {{
                    alert('Failed to resolve alert.');
                    if (btn) {{ btn.disabled = false; btn.textContent = '✅ Mark Alert as Resolved'; }}
                }}
            }} catch(e) {{
                alert('Connection error.');
                if (btn) {{ btn.disabled = false; btn.textContent = '✅ Mark Alert as Resolved'; }}
            }}
        }}
    </script>
</body>
</html>"""
    return html
