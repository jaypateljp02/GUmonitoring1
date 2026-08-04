import sys
sys.path.append('C:/GroundUp/ground-up-monitoring')
import asyncio
from backend.database import SessionLocal
from backend.models.sensor import Sensor
from backend.routes.sensors import get_device_ai_summary

db = SessionLocal()
sensor = db.query(Sensor).filter(Sensor.active == True, Sensor.type == 'temperature').first()
print(f'Testing AI Summary for sensor: {sensor.name} (device_id: {sensor.device_id})')

try:
    res = asyncio.run(get_device_ai_summary(sensor.device_id, db))
    print('SUCCESS RESULT:')
    print(res)
except Exception as e:
    import traceback
    traceback.print_exc()
