import uuid
from datetime import datetime
from sqlalchemy import Column, Boolean, DateTime, ForeignKey, Enum, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from backend.database import Base

class Sensor(Base):
    __tablename__ = "sensors"
    __table_args__ = {"schema": "monitoring"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=True)
    room_id = Column(UUID(as_uuid=True), ForeignKey("monitoring.rooms.id"), nullable=True)
    type = Column(Enum("temperature", "humidity", name="sensor_type", schema="monitoring", create_type=False), nullable=False)
    min_threshold = Column(Numeric)
    max_threshold = Column(Numeric)
    device_id = Column(String(50), nullable=True)
    mock_mode = Column(String(50), default="normal", server_default="normal", nullable=False)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

