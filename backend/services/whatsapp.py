"""
DEPRECATED — This module has been replaced by groundup_webhooks.alert_sender.

All WhatsApp functionality is now centralized in groundup_webhooks package:
  - groundup_webhooks.alert_sender   → send_monitoring_alert(), send_daily_summary(), calculate_priority()
  - groundup_webhooks.whatsapp_client → send_whatsapp_template_sync(), send_whatsapp_text_sync()

This file exists only as a backwards-compatibility redirect.
Do NOT add new code here.
"""
import os
import sys

# Ensure groundup_webhooks is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

# Re-export from the unified module so old imports keep working
from groundup_webhooks.alert_sender import (
    calculate_priority,
    send_monitoring_alert as send_whatsapp_alert,
    send_daily_summary as send_whatsapp_daily_summary,
)

__all__ = [
    "calculate_priority",
    "send_whatsapp_alert",
    "send_whatsapp_daily_summary",
]
