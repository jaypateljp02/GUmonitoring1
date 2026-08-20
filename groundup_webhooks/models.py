"""
SQLAlchemy Models for webhooks schema.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from groundup_webhooks.database import Base


class WebhookEvent(Base):
    __tablename__ = "events"
    __table_args__ = {"schema": "webhooks"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    app_name = Column(String(50), nullable=False)
    event_type = Column(String(100), nullable=False)
    payload = Column(JSONB, nullable=False)
    actor_id = Column(UUID(as_uuid=True), nullable=True)
    actor_name = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class WhatsAppMessage(Base):
    __tablename__ = "whatsapp_messages"
    __table_args__ = {"schema": "webhooks"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    direction = Column(String(10), nullable=False)  # 'incoming', 'outgoing'
    phone_number = Column(String(20), nullable=False)
    sender_name = Column(String(100), nullable=True)
    message_type = Column(String(20), nullable=False)  # 'text', 'audio', 'image', 'video'
    raw_content = Column(Text, nullable=True)
    media_path = Column(String(500), nullable=True)
    language = Column(String(10), default="hinglish")
    ai_intent = Column(String(100), nullable=True)
    extracted_data = Column(JSONB, nullable=True)
    status = Column(String(20), default="processed")
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class WebhookRecipient(Base):
    __tablename__ = "recipients"
    __table_args__ = {"schema": "webhooks"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=True)
    phone_number = Column(String(20), nullable=False, unique=True)
    display_name = Column(String(100), nullable=False)
    role = Column(String(50), default="employee")  # 'owner', 'admin', 'employee'
    preferred_lang = Column(String(10), default="hinglish")  # 'en', 'hi', 'mr', 'bn', 'hinglish'
    subscribed_events = Column(ARRAY(String), default=["*"])
    is_active = Column(Boolean, default=True)
    alert_group = Column(Integer, default=1)  # 1 = all alerts, 2 = critical only + daily reports
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class EpisodicMemory(Base):
    __tablename__ = "memory_episodic"
    __table_args__ = {"schema": "webhooks"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type = Column(String(100), nullable=False)
    subject = Column(String(100), nullable=False)
    summary = Column(Text, nullable=False)
    details = Column(JSONB, default=dict)
    occurred_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class SemanticMemory(Base):
    __tablename__ = "memory_semantic"
    __table_args__ = {"schema": "webhooks"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_name = Column(String(100), nullable=False)
    fact = Column(Text, nullable=False)
    graph_links = Column(JSONB, default=list)
    confidence = Column(Float, default=0.85)
    last_updated = Column(DateTime(timezone=True), default=datetime.utcnow)


class ProceduralMemory(Base):
    __tablename__ = "memory_procedural"
    __table_args__ = {"schema": "webhooks"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_name = Column(String(100), nullable=False)
    condition_expr = Column(Text, nullable=False)
    action_workflow = Column(Text, nullable=False)
    times_triggered = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)


class CircuitBreaker(Base):
    __tablename__ = "circuit_breaker"
    __table_args__ = {"schema": "webhooks"}

    service_name = Column(String(50), primary_key=True)
    failure_count = Column(Integer, default=0)
    is_open = Column(Boolean, default=False)
    last_failure = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow)
