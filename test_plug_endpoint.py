
import sys
sys.path.append("C:/GroundUp/ground-up-monitoring")
import traceback
try:
    from backend.database import SessionLocal
    from backend.models.sensor import Sensor
    from backend.models.plug_telemetry import PlugTelemetry
    
    db = SessionLocal()
    
    # Try what the endpoint does
    device_id = "1002912a22"  # Black Fridge
    sensor = db.query(Sensor).filter(Sensor.device_id == device_id, Sensor.active == True).first()
    print(f"Sensor found: {sensor}")
    print(f"Sensor name: {sensor.name if sensor else 'N/A'}")
    print(f"Sensor tapo_status: {sensor.tapo_status if sensor else 'N/A'}")
    
    last_log = db.query(PlugTelemetry).filter(
        PlugTelemetry.device_id == device_id
    ).order_by(PlugTelemetry.timestamp.desc()).first()
    
    print(f"Last plug log: {last_log}")
    if last_log:
        print(f"  timestamp: {last_log.timestamp}")
        print(f"  apower: {last_log.apower}")
        print(f"  voltage: {last_log.voltage}")
        
        from datetime import datetime
        is_stale = (datetime.utcnow() - last_log.timestamp).total_seconds() > 600.0
        print(f"  is_stale: {is_stale} (age: {(datetime.utcnow() - last_log.timestamp).total_seconds():.0f}s)")
        
        switch_state = "on"
        if sensor and sensor.tapo_status in ["on", "off"]:
            switch_state = sensor.tapo_status
        print(f"  switch_state: {switch_state}")
    else:
        print("NO PLUG TELEMETRY FOUND!")
    
    db.close()
    print("DB test completed successfully!")
except Exception as e:
    traceback.print_exc()
