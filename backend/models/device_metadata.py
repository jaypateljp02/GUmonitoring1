import uuid
from datetime import datetime, date
from sqlalchemy import Column, Text, Boolean, DateTime, Date, ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import UUID
from backend.database import Base

class DeviceDailyMetadata(Base):
    __tablename__ = "device_daily_metadata"
    __table_args__ = {"schema": "monitoring"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sensor_id = Column(UUID(as_uuid=True), ForeignKey("monitoring.sensors.id"), nullable=True)
    room_id = Column(UUID(as_uuid=True), ForeignKey("monitoring.rooms.id"), nullable=True)
    device_id = Column(Text, nullable=True)
    date = Column(Date, nullable=False, index=True)
    
    room_name = Column(Text, nullable=True)
    room_type = Column(Text, nullable=True)
    short_summary = Column(Text, nullable=True)
    
    has_issues = Column(Boolean, default=False)
    issue_type = Column(Text, nullable=True)  # e.g., 'temp_high', 'temp_low', 'offline', 'overworking', 'healthy'
    issue_duration_hours = Column(Numeric, default=0.0)
    
    t_avg = Column(Numeric, nullable=True)
    t_min = Column(Numeric, nullable=True)
    t_max = Column(Numeric, nullable=True)
    
    below_min_hours = Column(Numeric, default=0.0)
    above_max_hours = Column(Numeric, default=0.0)
    
    runtime_hours = Column(Numeric, nullable=True)
    starts_count = Column(Numeric, nullable=True)
    energy_kwh = Column(Numeric, nullable=True)
    
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
