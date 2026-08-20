"""
WhatsApp Cloud API client for groundup-webhooks.
Single source of truth for ALL outgoing WhatsApp messages across Ground Up apps.
Handles sending text messages, template messages, and logs all outgoing traffic.
Integrated with Circuit Breaker for all paths (async + sync).
"""
import logging
import httpx
from typing import Optional, List
from sqlalchemy.orm import Session

from groundup_webhooks.config import (
    META_API_VERSION,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_ACCESS_TOKEN,
)
from groundup_webhooks.circuit_breaker import is_circuit_open, record_success, record_failure
from groundup_webhooks.models import WhatsAppMessage

logger = logging.getLogger("groundup_webhooks.whatsapp_client")

API_URL = f"https://graph.facebook.com/{META_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"

HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
    "Content-Type": "application/json",
}


def _build_template_payload(
    phone: str,
    template_name: str,
    language_code: str,
    body_parameters: List[str],
    button_parameters: Optional[List[str]] = None,
) -> dict:
    """Build Meta WhatsApp template payload — shared by async and sync senders."""
    components = []
    if body_parameters:
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": str(p)} for p in body_parameters],
        })
    if button_parameters:
        for idx, btn_param in enumerate(button_parameters):
            components.append({
                "type": "button",
                "sub_type": "url",
                "index": str(idx),
                "parameters": [{"type": "text", "text": str(btn_param)}],
            })
    return {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
            "components": components,
        },
    }


def _log_outgoing(db: Session, phone: str, msg_type: str, content: str, sender_name: str):
    """Log outgoing message to webhooks.whatsapp_messages table."""
    try:
        msg = WhatsAppMessage(
            direction="outgoing",
            phone_number=phone,
            sender_name=sender_name,
            message_type=msg_type,
            raw_content=content,
            status="sent",
        )
        db.add(msg)
        db.commit()
    except Exception as e:
        logger.warning(f"Failed to log outgoing message: {e}")
        try:
            db.rollback()
        except Exception:
            pass


# ─── Synchronous Senders (used by monitoring worker, event bus) ─────────────────

def send_whatsapp_template_sync(
    phone_number: str,
    template_name: str,
    language_code: str,
    body_parameters: List[str],
    db: Session,
    sender_name: str = "System",
    button_parameters: Optional[List[str]] = None,
) -> bool:
    """Send a Meta-approved WhatsApp template message synchronously.
    Includes circuit breaker check and message logging.
    """
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        logger.error("WhatsApp credentials not configured.")
        return False

    if is_circuit_open(db):
        logger.warning(f"Circuit open: Skipping template '{template_name}' to {phone_number}")
        return False

    clean_phone = phone_number.replace("+", "").replace(" ", "").strip()
    payload = _build_template_payload(clean_phone, template_name, language_code, body_parameters, button_parameters)

    try:
        res = httpx.post(API_URL, json=payload, headers=HEADERS, timeout=10.0)
        if res.status_code in (200, 201):
            record_success(db)
            _log_outgoing(db, clean_phone, "template", f"Template: {template_name} | Params: {body_parameters}", sender_name)
            logger.info(f"✅ WhatsApp template '{template_name}' ({language_code}) sent to {clean_phone}")
            return True
        else:
            err = f"API {res.status_code}: {res.text}"
            logger.error(f"❌ Template send failed to {clean_phone}: {err}")
            record_failure(db, err)
            return False
    except Exception as e:
        logger.error(f"❌ Template send error to {clean_phone}: {e}")
        record_failure(db, str(e))
        return False


def send_whatsapp_text_sync(
    phone_number: str,
    text_body: str,
    db: Session,
    sender_name: str = "System",
) -> bool:
    """Send a free-text WhatsApp message synchronously.
    Only works within Meta's 24-hour conversation window.
    Includes circuit breaker check and message logging.
    """
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return False

    if is_circuit_open(db):
        logger.warning(f"Circuit open: Skipping text to {phone_number}")
        return False

    clean_phone = phone_number.replace("+", "").replace(" ", "").strip()
    payload = {
        "messaging_product": "whatsapp",
        "to": clean_phone,
        "type": "text",
        "text": {"body": text_body},
    }

    try:
        res = httpx.post(API_URL, json=payload, headers=HEADERS, timeout=10.0)
        if res.status_code in (200, 201):
            record_success(db)
            _log_outgoing(db, clean_phone, "text", text_body, sender_name)
            logger.info(f"✅ WhatsApp text sent to {clean_phone}")
            return True
        else:
            err = f"API {res.status_code}: {res.text}"
            logger.error(f"❌ Text send failed to {clean_phone}: {err}")

            # Fallback to template if 24h window is closed
            try:
                logger.info(f"Attempting template fallback to {clean_phone}")
                return send_whatsapp_template_sync(
                    phone_number=clean_phone,
                    template_name="task_notification_v1",
                    language_code="en",
                    body_parameters=["System Notification", text_body[:100], "Staff", sender_name, "Pending"],
                    button_parameters=["tasks/details"],
                    db=db,
                    sender_name=sender_name,
                )
            except Exception as fb_err:
                logger.error(f"Template fallback also failed: {fb_err}")

            record_failure(db, err)
            return False
    except Exception as e:
        logger.error(f"❌ Text send error: {e}")
        record_failure(db, str(e))
        return False


# ─── Async Senders (used by admin API, webhook handler) ─────────────────────────

async def send_whatsapp_template(
    phone_number: str,
    template_name: str,
    language_code: str,
    body_parameters: List[str],
    db: Session,
    sender_name: str = "System",
    button_parameters: Optional[List[str]] = None,
) -> bool:
    """Send a Meta-approved WhatsApp template message asynchronously."""
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        logger.error("WhatsApp credentials not configured.")
        return False

    if is_circuit_open(db):
        logger.warning(f"Circuit open: Skipping template '{template_name}' to {phone_number}")
        return False

    clean_phone = phone_number.replace("+", "").replace(" ", "").strip()
    payload = _build_template_payload(clean_phone, template_name, language_code, body_parameters, button_parameters)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(API_URL, json=payload, headers=HEADERS)

        if res.status_code in (200, 201):
            record_success(db)
            _log_outgoing(db, clean_phone, "template", f"Template: {template_name} | Params: {body_parameters}", sender_name)
            logger.info(f"✅ Async template '{template_name}' ({language_code}) sent to {clean_phone}")
            return True
        else:
            err = f"API {res.status_code}: {res.text}"
            logger.error(f"❌ Async template send failed: {err}")
            record_failure(db, err)
            return False
    except Exception as e:
        logger.error(f"❌ Async template error: {e}")
        record_failure(db, str(e))
        return False


async def send_whatsapp_text(
    phone_number: str,
    text_body: str,
    db: Session,
    sender_name: str = "System",
) -> bool:
    """Send a free-text WhatsApp message asynchronously."""
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        logger.error("WhatsApp credentials not configured.")
        return False

    if is_circuit_open(db):
        logger.warning(f"Circuit open: Skipping text to {phone_number}")
        return False

    clean_phone = phone_number.replace("+", "").replace(" ", "").strip()
    payload = {
        "messaging_product": "whatsapp",
        "to": clean_phone,
        "type": "text",
        "text": {"body": text_body},
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(API_URL, json=payload, headers=HEADERS)

        if res.status_code in (200, 201):
            record_success(db)
            _log_outgoing(db, clean_phone, "text", text_body, sender_name)
            logger.info(f"✅ Async text sent to {clean_phone}")
            return True
        else:
            err = f"API {res.status_code}: {res.text}"
            logger.error(f"❌ Async text send failed: {err}")

            # Fallback to template
            try:
                return send_whatsapp_template_sync(
                    phone_number=clean_phone,
                    template_name="task_notification_v1",
                    language_code="en",
                    body_parameters=["System Notification", text_body[:100], "Staff", sender_name, "Pending"],
                    button_parameters=["tasks/details"],
                    db=db,
                    sender_name=sender_name,
                )
            except Exception:
                pass

            record_failure(db, err)
            return False
    except Exception as e:
        logger.error(f"❌ Async text error: {e}")
        record_failure(db, str(e))
        return False
