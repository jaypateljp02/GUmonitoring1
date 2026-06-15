import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Numeric
from sqlalchemy.dialects.postgresql import UUID
from backend.database import Base

class DeviceTelemetry(Base):
    __tablename__ = "device_telemetry"
    __table_args__ = {"schema": "monitoring"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(String(50), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    temperature = Column(Numeric, nullable=False)
    humidity = Column(Numeric, nullable=False)
    battery_level = Column(Numeric, nullable=False)
