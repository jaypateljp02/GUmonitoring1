from backend.database import SessionLocal
from backend.models.device_metadata import DeviceDailyMetadata

db = SessionLocal()
count = db.query(DeviceDailyMetadata).count()
latest = db.query(DeviceDailyMetadata).order_by(DeviceDailyMetadata.date.desc()).first()
print('TOTAL_RECORDS:', count)
if latest:
    print('LATEST_DATE:', latest.date)
    print('LATEST_ROOM:', latest.room_name)

recent_5 = db.query(DeviceDailyMetadata).order_by(DeviceDailyMetadata.date.desc()).limit(10).all()
print('Recent daily metadata records:')
for r in recent_5:
    print('Date:', str(r.date), '| Room:', str(r.room_name), '| Avg Temp:', str(r.t_avg), '| Min:', str(r.t_min), '| Max:', str(r.t_max), '| Energy:', str(r.energy_kwh))

db.close()
