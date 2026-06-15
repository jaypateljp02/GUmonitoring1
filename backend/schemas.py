"""Pydantic schemas for Monitoring API."""
from datetime import datetime
from typing import Optional, List
from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel

class RoomCreate(BaseModel):
    name: str
    type: str
    description: Optional[str] = None

class SensorResponse(BaseModel):
    id: UUID
    room_id: Optional[UUID] = None
    type: str
    min_threshold: Optional[Decimal] = None
    max_threshold: Optional[Decimal] = None
    device_id: Optional[str] = None
    alert_webhook_url: Optional[str] = None
    recovery_webhook_url: Optional[str] = None
    tapo_ip: Optional[str] = None
    tapo_username: Optional[str] = None
    tapo_password: Optional[str] = None
    tapo_billing_rate: Optional[Decimal] = None
    active: bool
    created_at: datetime
    class Config:
        from_attributes = True

class SensorCreate(BaseModel):
    type: str
    min_threshold: Optional[Decimal] = None
    max_threshold: Optional[Decimal] = None

class SensorThresholdUpdate(BaseModel):
    min_threshold: Optional[Decimal] = None
    max_threshold: Optional[Decimal] = None

class ReadingResponse(BaseModel):
    id: UUID
    sensor_id: UUID
    value: Decimal
    recorded_at: datetime
    class Config:
        from_attributes = True

class ReadingCreate(BaseModel):
    value: Decimal

class RoomResponse(BaseModel):
    id: UUID
    name: str
    type: str
    description: Optional[str] = None
    map_x: Optional[str] = None
    map_y: Optional[str] = None
    active: bool
    created_at: datetime
    sensors: List[SensorResponse] = []
    class Config:
        from_attributes = True

class AlertResponse(BaseModel):
    id: UUID
    sensor_id: UUID
    value: Decimal
    message: Optional[str] = None
    resolved: bool
    created_at: datetime
    class Config:
        from_attributes = True

class AlertResolve(BaseModel):
    resolved: bool = True

class MessageResponse(BaseModel):
    message: str

class DeviceTelemetryResponse(BaseModel):
    id: UUID
    device_id: str
    timestamp: datetime
    temperature: Decimal
    humidity: Decimal
    battery_level: Decimal
    class Config:
        from_attributes = True

class MockControlRequest(BaseModel):
    mode: str  # 'normal', 'ice', 'warm', 'failover'

class DailyMetric(BaseModel):
    date: str
    temp_min: Optional[Decimal] = None
    temp_max: Optional[Decimal] = None
    hum_min: Optional[Decimal] = None
    hum_max: Optional[Decimal] = None

class MonthlyAnalyticsResponse(BaseModel):
    device_id: str
    month: int
    year: int
    daily_metrics: List[DailyMetric]

class DeviceMetrics24hResponse(BaseModel):
    device_id: str
    temp_avg: Optional[Decimal] = None
    temp_min: Optional[Decimal] = None
    temp_max: Optional[Decimal] = None
    hum_avg: Optional[Decimal] = None
    hum_min: Optional[Decimal] = None
    hum_max: Optional[Decimal] = None

class BatchContextResponse(BaseModel):
    device_id: str
    start_time: datetime
    end_time: datetime
    temp_avg: Optional[Decimal] = None
    temp_min: Optional[Decimal] = None
    temp_max: Optional[Decimal] = None
    hum_avg: Optional[Decimal] = None
    hum_min: Optional[Decimal] = None
    hum_max: Optional[Decimal] = None
    telemetry_logs: List[DeviceTelemetryResponse] = []

class OfflinePeriod(BaseModel):
    start: str
    end: str
    duration_minutes: int

class DeviceTelemetryHistoryResponse(BaseModel):
    telemetry: List[DeviceTelemetryResponse]
    offline_periods: List[OfflinePeriod]

class LoginRequest(BaseModel):
    email: str
    password: str

class UserResponseModel(BaseModel):
    id: UUID
    name: str
    email: str
    role: str

class LoginResponse(BaseModel):
    access_token: str
    user: UserResponseModel

