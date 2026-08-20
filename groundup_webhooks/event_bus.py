"""
Ground Up Event Bus — Shared Plug-and-Play Event Dispatcher.

Any microservice can emit an event in 1 line:
    from groundup_webhooks.event_bus import emit_event
    emit_event("tasks.task.created", {"title": "Clean miso room", "assigned_to_name": "Ravi"})
"""
import logging
import asyncio
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

from groundup_webhooks.database import SessionLocal
from groundup_webhooks.models import WebhookEvent, EpisodicMemory
from groundup_webhooks.recipient_resolver import resolve_recipients_for_event
from groundup_webhooks.message_formatter import format_event_message
from groundup_webhooks.whatsapp_client import send_whatsapp_text, send_whatsapp_template

logger = logging.getLogger("groundup_webhooks.event_bus")


def emit_event(
    event_type: str,
    payload: Dict[str, Any],
    app_name: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_name: Optional[str] = None,
    db: Optional[Session] = None
) -> str:
    """
    Core Plug-and-Play Event Emitter.
    1. Determines source app_name from event_type prefix if omitted (e.g. 'tasks.task.created' -> 'tasks')
    2. Logs event to webhooks.events table
    3. Logs episodic memory entry
    4. Finds recipients and dispatches WhatsApp messages asynchronously
    """
    if not app_name:
        app_name = event_type.split(".")[0] if "." in event_type else "system"

    should_close_db = False
    if db is None:
        db = SessionLocal()
        should_close_db = True

    try:
        # 1. Log event
        event_obj = WebhookEvent(
            app_name=app_name,
            event_type=event_type,
            payload=payload,
            actor_name=actor_name
        )
        db.add(event_obj)

        # 2. Episodic Memory Record
        summary_text = payload.get("text", payload.get("title", payload.get("message", f"Event {event_type}")))
        subject_text = payload.get("assigned_to_name", payload.get("sensor_name", actor_name or app_name))

        episodic = EpisodicMemory(
            event_type=event_type,
            subject=subject_text,
            summary=str(summary_text)[:500],
            details=payload
        )
        db.add(episodic)
        db.commit()
        db.refresh(event_obj)

        # Auto-create entry in tasks.tasks schema table
        if event_type == "tasks.task.created":
            try:
                import uuid
                from sqlalchemy import text
                title_val = str(payload.get("title", "New Task"))
                assigned_name = str(payload.get("assigned_to_name", "Staff"))
                full_title = f"{title_val} - Assigned to {assigned_name}" if assigned_name not in title_val else title_val
                
                user_res = db.execute(text("SELECT id FROM auth.users LIMIT 1")).fetchone()
                user_id = user_res[0] if user_res else "3bc9cd9f-a10b-4a4b-81b2-dee2891334af"

                db.execute(text("""
                    INSERT INTO tasks.tasks (id, title, description, assigned_to, created_by, status, created_at)
                    VALUES (:tid, :title, :desc, :uid, :uid, 'pending', NOW())
                """), {
                    "tid": str(uuid.uuid4()),
                    "title": full_title,
                    "desc": f"Assigned to {assigned_name}",
                    "uid": user_id
                })
                db.commit()
                logger.info(f"✨ Auto-created tasks.tasks record: {full_title}")
            except Exception as te:
                logger.warning(f"Failed to auto-create tasks.tasks entry: {te}")

        event_id = str(event_obj.id)
        logger.info(f"📡 Event emitted: [{app_name}] {event_type} (id={event_id})")

        # 3. Resolve Recipients
        recipients = resolve_recipients_for_event(event_type, db)

        # ─── IMPORTANT: Skip WhatsApp dispatch for monitoring events ────────
        # Monitoring alerts/summaries are handled exclusively by
        # groundup_webhooks.alert_sender (via worker.py / insights.py).
        # The event bus only LOGS the event + episodic memory for monitoring.
        # This prevents double-sending.
        if event_type.startswith("monitoring."):
            logger.info(f"Monitoring event '{event_type}' logged. WhatsApp handled by alert_sender.")
            return event_id

        # For task creation, send the template card to the target employee AND the owner
        target_assigned = payload.get("assigned_to_name")
        if event_type == "tasks.task.created" and target_assigned:
            filtered = []
            for r in recipients:
                dname = r["display_name"].lower()
                target_lower = target_assigned.lower()
                role = (r.get("role") or "").lower()
                if role == "owner" or target_lower in dname or dname.split()[0] in target_lower or (("yadav" in target_lower or "yadhav" in target_lower) and "yadav" in dname):
                    filtered.append(r)
            if filtered:
                recipients = filtered

        # 4. Dispatch WhatsApp notifications
        if recipients:
            for r in recipients:
                phone = r["phone_number"]
                lang = r.get("preferred_lang", "hinglish")
                
                # Check if event has an approved template variant (e.g. jar quality, task assigned, issue reported)
                TEMPLATE_EVENT_MAP = {
                    "production.jar.quality_alert": {
                        "en": "jar_quality_alert_v1", "hinglish": "jar_quality_alert_v1", "hindi": "jar_quality_alert_v1", "marathi": "jar_quality_alert_mr", "bengali": "jar_quality_alert_bn"
                    },
                    "tasks.task.created": {
                        "en": "task_notification_v1", "hinglish": "task_notification_v1", "hindi": "task_notification_v1", "marathi": "task_notification_mr", "bengali": "task_notification_bn"
                    },
                    "tasks.issue.reported": {
                        "en": "issue_reported_v1", "hinglish": "issue_reported_v1", "hindi": "issue_reported_v1", "marathi": "issue_reported_mr", "bengali": "issue_reported_bn"
                    }
                }

                lang_template_map = TEMPLATE_EVENT_MAP.get(event_type)
                template_to_send = lang_template_map.get(lang, lang_template_map.get("en")) if lang_template_map else None

                if template_to_send:
                    # Build parameters based on event payload
                    if event_type == "production.jar.quality_alert":
                        body_params = [
                            str(payload.get("jar_number", "N/A")),
                            str(payload.get("smell", "normal")),
                            str(payload.get("taste", "normal")),
                            str(payload.get("recorded_by", actor_name or "Staff")),
                            str(payload.get("observation", "Quality Alert"))
                        ]
                        button_params = [str(payload.get("jar_number", "active"))]
                    elif event_type == "tasks.task.created":
                        body_params = [
                            "New Assignment",
                            str(payload.get("title", "Task")),
                            str(payload.get("assigned_to_name", "Staff")),
                            str(payload.get("created_by", actor_name or "Manager")),
                            "Pending"
                        ]
                        button_params = ["tasks/details"]
                    elif event_type == "tasks.issue.reported":
                        body_params = [
                            str(payload.get("title", "Floor Issue")),
                            str(payload.get("reporter_name", actor_name or "Staff")),
                            str(payload.get("severity", "High")),
                            "Just now"
                        ]
                        button_params = ["issues"]
                    else:
                        body_params = [str(payload.get("title", "Notification"))]
                        button_params = ["home"]

                    lang_code_map = {"marathi": "mr", "bengali": "bn", "hindi": "hi", "hinglish": "en", "en": "en"}
                    l_code = lang_code_map.get(lang, "en")

                    from groundup_webhooks.whatsapp_client import send_whatsapp_template_sync, send_whatsapp_text_sync
                    try:
                        send_whatsapp_template_sync(
                            phone_number=phone,
                            template_name=template_to_send,
                            language_code=l_code,
                            body_parameters=body_params,
                            button_parameters=button_params,
                            db=db,
                            sender_name="GroundUp Bot"
                        )
                    except Exception as wa_err:
                        logger.error(f"Template dispatch failed to {phone}: {wa_err}")
                else:
                    formatted_msg = format_event_message(event_type, payload, lang=lang)
                    from groundup_webhooks.whatsapp_client import send_whatsapp_text_sync
                    try:
                        send_whatsapp_text_sync(phone, formatted_msg, db, sender_name="GroundUp Bot")
                    except Exception as txt_err:
                        logger.warning(f"Direct text dispatch failed to {phone}: {txt_err}")

        return event_id

    except Exception as e:
        logger.error(f"Error emitting event {event_type}: {e}", exc_info=True)
        if db:
            db.rollback()
        return ""
    finally:
        if should_close_db and db:
            db.close()
