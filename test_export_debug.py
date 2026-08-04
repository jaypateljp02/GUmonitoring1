
import sys, traceback
sys.path.insert(0, r'C:\GroundUp\ground-up-monitoring')
from backend.database import SessionLocal
from backend.routes.sensors import export_device_telemetry

db = SessionLocal()
try:
    res = export_device_telemetry('a4b0028d6f', 1, 1, None, None, db)
    print("SUCCESS", len(res.body))
except Exception:
    traceback.print_exc()
