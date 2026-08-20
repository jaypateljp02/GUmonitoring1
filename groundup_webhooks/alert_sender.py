"""
Unified Monitoring Alert & Daily Summary Sender for Ground Up.

This module replaces the old monitoring/services/whatsapp.py entirely.
All monitoring WhatsApp messages go through here → whatsapp_client.py → Meta API.

Features:
  - Single recipient source: webhooks.recipients table only
  - Group 1/Group 2 severity filtering
  - Per-recipient language-specific templates
  - Circuit breaker protection (via whatsapp_client)
  - Full message logging (via whatsapp_client)
  - No double-sending

Template names verified against Meta Business Account (Aug 2026):
  Alert:   fermentary_alert_v1 (en), fermentary_alert_hindi (hi),
           fermentary_alert_marathi (mr), fermentary_alert_bn (bn)
  Summary: fermentary_daily_summary_en (en), fermentary_daily_summary_hi (hi),
           fermentary_daily_summary_marathi (mr), fermentary_daily_summary_bn (bn)
"""
import logging
from typing import Optional

from groundup_webhooks.database import SessionLocal
from groundup_webhooks.recipient_resolver import resolve_monitoring_recipients
from groundup_webhooks.whatsapp_client import send_whatsapp_template_sync

logger = logging.getLogger("groundup_webhooks.alert_sender")


# ─── Template Mappings (verified against Meta WABA) ─────────────────────────────

ALERT_TEMPLATES = {
    "en":       "fermentary_alert_v1",
    "hinglish": "fermentary_alert_v1",
    "hindi":    "fermentary_alert_hindi",
    "marathi":  "fermentary_alert_marathi",
    "bengali":  "fermentary_alert_bn",
}

SUMMARY_TEMPLATES = {
    "en":       "fermentary_daily_summary_en",
    "hinglish": "fermentary_daily_summary_en",
    "hindi":    "fermentary_daily_summary_hi",
    "marathi":  "fermentary_daily_summary_marathi",
    "bengali":  "fermentary_daily_summary_bn",
}

LANG_CODES = {
    "en":       "en",
    "hinglish": "en",
    "hindi":    "hi",
    "marathi":  "mr",
    "bengali":  "bn",
}


# ─── Priority Calculator ────────────────────────────────────────────────────────

def calculate_priority(room_type: str, sensor_type: str, value: float, sensor) -> str:
    """
    Calculate alert priority based on room type, sensor type, and severity of threshold deviation.
    
    Priority Levels:
      - Critical: Fridge/freezer temp deviates > 10°C beyond threshold (high-high)
      - High:     Fridge/freezer threshold violation or offline
      - Medium:   Room-type threshold violation
      - Low:      Room-type offline
    """
    room_lower = (room_type or "").lower()
    sensor_lower = (sensor_type or "").lower()

    # Offline alerts
    if sensor_lower == "offline":
        return "High" if room_lower in ("fridge", "freezer") else "Low"

    # Temperature/humidity threshold violations
    if room_lower in ("fridge", "freezer"):
        if sensor_lower == "temperature":
            min_th = float(sensor.min_threshold) if sensor.min_threshold is not None else None
            max_th = float(sensor.max_threshold) if sensor.max_threshold is not None else None
            val = float(value)

            # Critical = High-High: temp deviates > 10°C beyond max threshold
            if max_th is not None and val > (max_th + 10.0):
                return "Critical"
            return "High"
        else:
            return "High"  # Humidity in fridge/freezer
    elif room_lower == "room":
        return "Medium"

    return "Medium"


# ─── Alert Sender ────────────────────────────────────────────────────────────────

def send_monitoring_alert(
    sensor_name: str,
    alert_type: str,
    current_value: str,
    normal_range: str,
    duration: str,
    priority: str,
    alert_id: str,
):
    """
    Send a monitoring alert to the appropriate recipient groups.

    Routing Logic:
      - Normal/Medium/High priority → Group 1 only
      - Critical (High-High) priority → Group 1 AND Group 2
    """
    # Group 1 gets ALL alerts; Group 2 gets ONLY Critical (High-High) alerts
    target_groups = [1]
    if priority == "Critical":
        target_groups.append(2)

    db = SessionLocal()
    try:
        recipients = resolve_monitoring_recipients(db, target_groups)

        if not recipients:
            logger.warning("No monitoring recipients found. Alert not sent.")
            return

        body_params = [sensor_name, alert_type, current_value, normal_range, duration, priority]
        button_params = [str(alert_id)] if alert_id else ["active"]

        sent_count = 0
        for r in recipients:
            lang = r["preferred_lang"]
            template = ALERT_TEMPLATES.get(lang, ALERT_TEMPLATES["en"])
            lang_code = LANG_CODES.get(lang, "en")

            ok = send_whatsapp_template_sync(
                phone_number=r["phone_number"],
                template_name=template,
                language_code=lang_code,
                body_parameters=body_params,
                button_parameters=button_params,
                db=db,
                sender_name="Monitoring Alert",
            )
            if ok:
                sent_count += 1

        logger.info(
            f"📡 Alert dispatched: '{sensor_name}' ({priority}) → "
            f"{sent_count}/{len(recipients)} recipients (groups={target_groups})"
        )
    except Exception as e:
        logger.error(f"Error sending monitoring alert: {e}", exc_info=True)
    finally:
        db.close()


# ─── Daily Summary Sender ───────────────────────────────────────────────────────

def send_daily_summary(
    date_str: str,
    normal_count: int,
    active_count: int,
    highest_priority: str,
    ai_summary: str,
):
    """
    Send the daily monitoring summary to Group 1 AND Group 2.

    The ai_summary should be a SHORT, clean message (not raw data dump).
    Max 800 chars due to Meta template parameter limits.
    """
    import re

    # Sanitize for Meta API: no newlines/tabs, no 4+ consecutive spaces
    sanitized = re.sub(r'[\n\t\r]', ' ', ai_summary)
    sanitized = re.sub(r'\s{2,}', ' ', sanitized).strip()
    trimmed = sanitized[:800] + ('...' if len(sanitized) > 800 else '')

    db = SessionLocal()
    try:
        # Daily summary goes to BOTH groups
        recipients = resolve_monitoring_recipients(db, [1, 2])

        if not recipients:
            logger.warning("No monitoring recipients found. Daily summary not sent.")
            return

        body_params = [
            date_str,
            str(normal_count),
            str(active_count),
            highest_priority,
            trimmed,
        ]
        button_params = ["reports/preview"]

        sent_count = 0
        for r in recipients:
            lang = r["preferred_lang"]
            template = SUMMARY_TEMPLATES.get(lang, SUMMARY_TEMPLATES["en"])
            lang_code = LANG_CODES.get(lang, "en")

            ok = send_whatsapp_template_sync(
                phone_number=r["phone_number"],
                template_name=template,
                language_code=lang_code,
                body_parameters=body_params,
                button_parameters=button_params,
                db=db,
                sender_name="Daily Summary",
            )
            if ok:
                sent_count += 1

        logger.info(
            f"📊 Daily summary dispatched for {date_str} → "
            f"{sent_count}/{len(recipients)} recipients"
        )
    except Exception as e:
        logger.error(f"Error sending daily summary: {e}", exc_info=True)
    finally:
        db.close()
