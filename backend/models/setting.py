from sqlalchemy import Column, String, DateTime, text
from backend.database import Base

class Setting(Base):
    __tablename__ = "settings"
    __table_args__ = {"schema": "monitoring"}

    key = Column(String(100), primary_key=True)
    value = Column(String(255), nullable=False)
    description = Column(String(500), nullable=True)
    updated_at = Column(DateTime, server_default=text("now()"), onupdate=text("now()"))

    def __repr__(self):
        return f"<Setting {self.key}={self.value}>"
