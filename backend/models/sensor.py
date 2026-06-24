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
    type = Column(Enum("temperature", "humidity", name="sensor_type", create_type=False), nullable=False)
    min_threshold = Column(Numeric)
    max_threshold = Column(Numeric)
    device_id = Column(String(50), nullable=True)
    alert_webhook_url = Column(String(500), nullable=True)
    recovery_webhook_url = Column(String(500), nullable=True)
    tapo_ip = Column(String(50), nullable=True)
    tapo_username = Column(String(100), nullable=True)
    tapo_password = Column(String(100), nullable=True)
    tapo_billing_rate = Column(Numeric, default=10.0, server_default="10.0", nullable=True)
    tapo_running_threshold = Column(Numeric, default=80.0, server_default="80.0", nullable=True)
    mock_mode = Column(String(50), default="normal", server_default="normal", nullable=False)
    active = Column(Boolean, default=True)
    tapo_last_seen = Column(DateTime, nullable=True)
    tapo_status = Column(String(50), nullable=True)
    tapo_error = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
