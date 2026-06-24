import uuid
from sqlalchemy import Column, DateTime, ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import UUID
from backend.database import Base

class DoorEvent(Base):
    __tablename__ = "door_events"
    __table_args__ = {"schema": "monitoring"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sensor_id = Column(UUID(as_uuid=True), ForeignKey("monitoring.sensors.id"), nullable=True)
    opened_at = Column(DateTime, nullable=False)
    closed_at = Column(DateTime, nullable=False)
    duration_minutes = Column(Numeric, nullable=False)
