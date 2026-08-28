import re
import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import text

from groundup_webhooks.models import WhatsAppMessage
from groundup_webhooks.whatsapp_client import send_whatsapp_text, send_whatsapp_text_sync
from groundup_webhooks.event_bus import emit_event

logger = logging.getLogger("groundup_webhooks.escalation_engine")


def is_active_escalation(sender_phone: str, db: Session) -> bool:
    """Check if a phone number currently has an open escalation session."""
    clean_phone = sender_phone.replace("+", "").replace(" ", "").strip()
    row = db.execute(text("""
        SELECT id FROM webhooks.escalation_sessions
        WHERE sender_phone = :ph AND status = 'ACTIVE'
        AND created_at >= (NOW() AT TIME ZONE 'utc' - INTERVAL '24 hours')
        LIMIT 1
    """), {"ph": clean_phone}).fetchone()
    return bool(row)


async def check_and_handle_owner_reply(
    sender_phone: str,
    raw_text: str,
    db: Session
) -> Optional[str]:
    """
    Checks if an incoming message is from an Owner replying to an escalated employee query.
    Syntax supported:
      1. 'reply <phone_number> <message>' or 'r <phone_number> <message>'
      2. 'resolve <phone_number>' or 'close <phone_number>'
      3. Direct message to latest open escalation if sender is an Owner.
    """
    clean_sender = sender_phone.replace("+", "").replace(" ", "").strip()
    lower = raw_text.lower().strip()

    # 1. Match explicit command: 'reply 9876543210 Hello Ravi, please...'
    match_reply = re.search(r"^(?:reply|jawab|r)\s+(\+?\d{10,13})\s+(.*)$", raw_text, flags=re.IGNORECASE | re.DOTALL)
    if match_reply:
        target_phone = match_reply.group(1).replace("+", "").replace(" ", "").strip()
        reply_body = match_reply.group(2).strip()
        
        # Check active session
        session_row = db.execute(text("""
            SELECT id, sender_name FROM webhooks.escalation_sessions
            WHERE sender_phone LIKE :tph AND status = 'ACTIVE'
            ORDER BY created_at DESC LIMIT 1
        """), {"tph": f"%{target_phone[-10:]}"}).fetchone()

        # Send to employee
        employee_msg = (
            f"💬 *Message from Management (Owner/Gaya):*\n"
            f"{reply_body}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ _Your escalation has been marked resolved. Bandhu automated assistant is now active again._"
        )
        send_whatsapp_text_sync(target_phone, employee_msg, db, sender_name="Management Reply")

        # Mark session resolved
        if session_row:
            db.execute(text("""
                UPDATE webhooks.escalation_sessions
                SET status = 'RESOLVED', resolved_at = (NOW() AT TIME ZONE 'utc')
                WHERE id = :sid
            """), {"sid": session_row[0]})
            db.commit()

        # Log episodic memory
        emp_name = session_row[1] if session_row else target_phone
        db.execute(text("""
            INSERT INTO webhooks.memory_episodic (id, event_type, subject, summary, details, occurred_at)
            VALUES (:eid, 'escalation.resolved', :subj, :summ, CAST(:pld AS jsonb), NOW())
        """), {
            "eid": str(uuid.uuid4()),
            "subj": f"Escalation for {emp_name}",
            "summ": f"Owner replied to {emp_name}: '{reply_body[:100]}'",
            "pld": '{"resolved": true}'
        })
        db.commit()

        return f"✅ *Reply delivered to {emp_name} (+{target_phone})* and escalation marked resolved."

    # 2. Match explicit resolve command: 'resolve 9876543210'
    match_resolve = re.search(r"^(?:resolve|close|khatam)\s+(\+?\d{10,13})$", lower)
    if match_resolve:
        target_phone = match_resolve.group(1).replace("+", "").replace(" ", "").strip()
        db.execute(text("""
            UPDATE webhooks.escalation_sessions
            SET status = 'RESOLVED', resolved_at = (NOW() AT TIME ZONE 'utc')
            WHERE sender_phone LIKE :tph AND status = 'ACTIVE'
        """), {"tph": f"%{target_phone[-10:]}"})
        db.commit()
        return f"✅ *Escalation session for +{target_phone} closed successfully.*"

    return None


async def escalate_to_human(
    sender_phone: str,
    sender_name: str,
    user_query: str,
    reason: str,
    db: Session
) -> str:
    """
    Creates an escalation session and alerts the Owner / Management team on WhatsApp.
    """
    clean_phone = sender_phone.replace("+", "").replace(" ", "").strip()

    # Create / Update active session
    session_id = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO webhooks.escalation_sessions (id, sender_phone, sender_name, reason, last_user_query, status, created_at)
        VALUES (:id, :ph, :name, :reason, :query, 'ACTIVE', NOW() AT TIME ZONE 'utc')
    """), {
        "id": session_id,
        "ph": clean_phone,
        "name": sender_name,
        "reason": reason,
        "query": user_query
    })
    db.commit()

    # Query Group 1 / Owner recipients
    recipients = db.execute(text("""
        SELECT phone_number, display_name FROM webhooks.recipients
        WHERE is_active = true AND (alert_group = 1 OR role ILIKE '%owner%' OR role ILIKE '%admin%')
    """)).fetchall()

    ist_now = (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%I:%M %p, %d %b")

    owner_alert = (
        f"🚨 *BANDHU ESCALATION: Assistance Needed* 🙋\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Employee:* {sender_name} (+{clean_phone})\n"
        f"⏰ *Time:* {ist_now}\n"
        f"❓ *Query / Issue:* \"{user_query}\"\n"
        f"📌 *Reason:* {reason}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👉 *To reply directly, message:* \n"
        f"`reply {clean_phone} <your message>`"
    )

    # Dispatch to owners
    sent_count = 0
    for r in recipients:
        r_phone = r[0].replace("+", "").replace(" ", "").strip()
        if r_phone != clean_phone:
            send_whatsapp_text_sync(r_phone, owner_alert, db, sender_name="Bandhu Escalation")
            sent_count += 1

    logger.info(f"Escalation created for {sender_name} ({clean_phone}). Alerted {sent_count} managers.")

    # Return reassurance card to employee
    return (
        f"🙋 *Transferred to Management (Owner / Gaya)* 📲\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Staff:* {sender_name}\n"
        f"📝 *Your Message:* \"{user_query}\"\n\n"
        f"✅ Management has been alerted with your request. They will reply to you directly on WhatsApp shortly."
    )
