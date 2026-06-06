import uuid
from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import UUID
from api.database import Base

class SensorReading(Base):
    __tablename__ = "sensor_readings"
    __table_args__ = {"schema": "monitoring"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sensor_id = Column(UUID(as_uuid=True), ForeignKey("monitoring.sensors.id"), nullable=False)
    value = Column(Numeric, nullable=False)
    recorded_at = Column(DateTime, default=datetime.utcnow)
