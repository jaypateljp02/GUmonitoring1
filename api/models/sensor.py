import uuid
from datetime import datetime
from sqlalchemy import Column, Boolean, DateTime, ForeignKey, Enum, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from api.database import Base

class Sensor(Base):
    __tablename__ = "sensors"
    __table_args__ = {"schema": "monitoring"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    room_id = Column(UUID(as_uuid=True), ForeignKey("monitoring.rooms.id"), nullable=False)
    type = Column(Enum("temperature", "humidity", name="sensor_type", schema="monitoring"), nullable=False)
    min_threshold = Column(Numeric)
    max_threshold = Column(Numeric)
    device_id = Column(String(50), nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

