import sys
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv
load_dotenv()
load_dotenv("backend/.env")
from backend.database import SessionLocal
from backend.models.setting import Setting
from backend.models.alert import Alert
from datetime import datetime, timedelta

db = SessionLocal()

# Check last_daily_report_date setting
setting = db.query(Setting).filter(Setting.key == "last_daily_report_date").first()
print("=== Daily Report Guard ===")
print(f"last_daily_report_date: {setting.value if setting else 'NOT FOUND'}")
print(f"Updated at (UTC): {setting.updated_at if setting else 'N/A'}")

ist = timedelta(hours=5, minutes=30)
if setting and setting.updated_at:
    print(f"Updated at (IST): {(setting.updated_at + ist).strftime('%Y-%m-%d %I:%M:%S %p')}")

now_utc = datetime.utcnow()
now_ist = now_utc + ist
today_str = now_ist.strftime("%Y-%m-%d")
print(f"\nCurrent IST: {now_ist.strftime('%Y-%m-%d %I:%M:%S %p')}")
print(f"Today string: {today_str}")
print(f"Guard match: setting.value ({setting.value if setting else 'N/A'}) == today ({today_str}) => {'BLOCKED (wont re-send)' if setting and setting.value == today_str else 'WOULD TRIGGER'}")

# Check recent alerts (last 24h)
cutoff = now_utc - timedelta(hours=24)
recent_alerts = db.query(Alert).filter(
    Alert.created_at >= cutoff
).order_by(Alert.created_at.desc()).all()

print(f"\n=== Alerts in Last 24h: {len(recent_alerts)} ===")
active_count = sum(1 for a in recent_alerts if not a.resolved)
resolved_count = sum(1 for a in recent_alerts if a.resolved)
print(f"  Active: {active_count}")
print(f"  Resolved: {resolved_count}")

# Group by message pattern
from collections import Counter
patterns = Counter()
for a in recent_alerts:
    msg = str(a.message or "")
    # Extract room name
    if "[" in msg and "]" in msg:
        room = msg[msg.index("[")+1:msg.index("]")]
    else:
        room = "Unknown"
    if "Temperature" in msg:
        patterns[f"{room} - Temperature alert"] += 1
    elif "Humidity" in msg:
        patterns[f"{room} - Humidity alert"] += 1
    elif "offline" in msg.lower():
        patterns[f"{room} - Offline alert"] += 1
    else:
        patterns[f"{room} - Other: {msg[:50]}"] += 1

print("\n=== Alert Summary (grouped) ===")
for pattern, count in patterns.most_common(20):
    print(f"  {count}x {pattern}")

# Check for duplicate daily reports by looking at WhatsApp send logs
# Check if the report was sent multiple times today
print("\n=== Checking for duplicate report indicators ===")
all_alerts_today = db.query(Alert).filter(
    Alert.created_at >= (now_utc - timedelta(hours=24))
).order_by(Alert.created_at.asc()).all()

report_trigger_alerts = [a for a in all_alerts_today if "report" in str(a.message or "").lower() or "daily" in str(a.message or "").lower()]
if report_trigger_alerts:
    print(f"  Found {len(report_trigger_alerts)} report-related alerts")
    for a in report_trigger_alerts:
        ts = (a.created_at + ist).strftime('%I:%M %p') if a.created_at else 'N/A'
        print(f"    {ts}: {str(a.message)[:100]}")
else:
    print("  No report-related alerts found in DB.")
    print("  Double-send likely caused by service restart during the 22:40+ window.")

db.close()
