from sqlalchemy import Column, String, DateTime
from backend.database import Base
from datetime import datetime

class EwelinkToken(Base):
    __tablename__ = "ewelink_tokens"
    __table_args__ = {"schema": "monitoring"}

    id = Column(String(50), primary_key=True, default="default")
    access_token = Column(String(500), nullable=False)
    refresh_token = Column(String(500), nullable=False)
    region = Column(String(20), default="as")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
