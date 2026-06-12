import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Numeric
from sqlalchemy.dialects.postgresql import UUID
from backend.database import Base

class PlugTelemetry(Base):
    __tablename__ = "plug_telemetry"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(String(50), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    apower = Column(Numeric, nullable=False)        # Active Power in Watts
    voltage = Column(Numeric, nullable=False)       # Voltage in Volts
    current = Column(Numeric, nullable=False)       # Current in Amperes
    today_energy = Column(Numeric, nullable=False)  # Energy consumed today in Wh
    month_energy = Column(Numeric, nullable=False)  # Energy consumed this month in Wh
