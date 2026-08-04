import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from backend.database import Base

class MaintenanceEvent(Base):
    __tablename__ = "maintenance_events"
    __table_args__ = {"schema": "monitoring"}
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    room_id = Column(UUID(as_uuid=True), ForeignKey("monitoring.rooms.id"))
    event_type = Column(String(50))  # "cleaning", "defrost", "maintenance"
    started_at = Column(DateTime)
    ended_at = Column(DateTime, nullable=True)  # NULL = still in progress
    reported_by_phone = Column(String(50))
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
