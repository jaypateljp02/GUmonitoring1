import uuid
from datetime import date
from sqlalchemy import Column, Date, ForeignKey, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID
from backend.database import Base

class CompressorStats(Base):
    __tablename__ = "compressor_stats"
    __table_args__ = {"schema": "monitoring"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sensor_id = Column(UUID(as_uuid=True), ForeignKey("monitoring.sensors.id"), nullable=True)
    date = Column(Date, nullable=False)
    cycle_count = Column(Integer, default=0)
    total_runtime_minutes = Column(Numeric, default=0.0)
    avg_runtime_per_cycle_minutes = Column(Numeric, default=0.0)
    daily_energy_kwh = Column(Numeric, default=0.0)
    monthly_energy_kwh = Column(Numeric, default=0.0)
    estimated_cost = Column(Numeric, default=0.0)
