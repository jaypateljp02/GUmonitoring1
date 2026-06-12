import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, Text, Enum
from sqlalchemy.dialects.postgresql import UUID
from backend.database import Base

class Room(Base):
    __tablename__ = "rooms"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    type = Column(Enum("room", "fridge", "freezer", name="room_type", create_type=False), nullable=False)
    description = Column(Text)
    map_x = Column(String(20), nullable=True)  # Percentage string like '45%'
    map_y = Column(String(20), nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
