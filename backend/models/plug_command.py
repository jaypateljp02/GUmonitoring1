import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from backend.database import Base


class PlugCommand(Base):
    """
    Command queue for Tapo plug toggle requests.

    When the web dashboard (running on Render/cloud) wants to toggle a Tapo plug,
    it cannot reach the local-network IP directly. Instead it writes a PlugCommand
    record here. The local background worker polls this table every loop and
    executes any pending commands via the Tapo LAN API, then marks them 'done'.
    """
    __tablename__ = "plug_commands"
    __table_args__ = {"schema": "monitoring"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(String(50), nullable=False, index=True)
    command = Column(String(10), nullable=False)   # 'on' or 'off'
    status = Column(String(20), nullable=False, default="pending")
    # status values: 'pending' → 'executing' → 'done' | 'failed'
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    executed_at = Column(DateTime, nullable=True)
    error = Column(String(500), nullable=True)
