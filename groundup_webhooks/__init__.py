"""
groundup-webhooks shared Python package.
Provides unified event bus and WhatsApp integration across all Ground Up apps.

Usage:
    from groundup_webhooks import emit_event
    from groundup_webhooks.alert_sender import send_monitoring_alert, send_daily_summary, calculate_priority
"""
from groundup_webhooks.event_bus import emit_event
from groundup_webhooks.config import (
    META_API_VERSION,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_BUSINESS_ACCOUNT_ID,
    WHATSAPP_VERIFY_TOKEN,
    WHATSAPP_ACCESS_TOKEN,
)

__all__ = [
    "emit_event",
    "META_API_VERSION",
    "WHATSAPP_PHONE_NUMBER_ID",
    "WHATSAPP_BUSINESS_ACCOUNT_ID",
    "WHATSAPP_VERIFY_TOKEN",
    "WHATSAPP_ACCESS_TOKEN",
]
