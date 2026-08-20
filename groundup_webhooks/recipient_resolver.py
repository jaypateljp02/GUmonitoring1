"""
Recipient Resolver for groundup-webhooks.
Determines which registered phone numbers should receive a specific event notification.

Group Logic (for monitoring alerts):
  - Group 1 (alert_group=1): Receives ALL monitoring alerts + daily summary
  - Group 2 (alert_group=2): Receives ALL monitoring alerts + daily summary
"""
import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from groundup_webhooks.models import WebhookRecipient

logger = logging.getLogger("groundup_webhooks.recipient_resolver")

# Default fallback recipient if table is empty
DEFAULT_OWNER_PHONE = "919421187857"


def resolve_recipients_for_event(event_type: str, db: Session) -> List[Dict[str, Any]]:
    """
    Returns a list of active recipient dicts for a given event type.
    Uses subscribed_events field to match against event_type patterns.
    [{'phone_number': '...', 'display_name': '...', 'role': '...', 'preferred_lang': '...'}]
    """
    recipients = db.query(WebhookRecipient).filter(WebhookRecipient.is_active == True).all()

    # If no recipients in DB yet, seed default owner
    if not recipients:
        logger.info(f"No recipients in DB. Seeding default owner ({DEFAULT_OWNER_PHONE})")
        default_owner = WebhookRecipient(
            phone_number=DEFAULT_OWNER_PHONE,
            display_name="Owner",
            role="owner",
            preferred_lang="hinglish",
            subscribed_events=["*"],
            alert_group=1,
        )
        db.add(default_owner)
        db.commit()
        db.refresh(default_owner)
        recipients = [default_owner]

    matched = []
    for r in recipients:
        if _is_subscribed(r, event_type):
            matched.append({
                "id": str(r.id),
                "phone_number": r.phone_number,
                "display_name": r.display_name,
                "role": r.role,
                "preferred_lang": r.preferred_lang or "hinglish",
                "alert_group": getattr(r, "alert_group", 1) or 1,
            })

    return matched


def resolve_monitoring_recipients(
    db: Session,
    target_groups: List[int],
) -> List[Dict[str, Any]]:
    """
    Resolve recipients specifically for monitoring alerts/reports.
    Filters by:
      1. is_active = True
      2. subscribed_events includes monitoring.* or *
      3. alert_group is in target_groups

    Parameters:
        target_groups: e.g. [1] for normal alerts, [1, 2] for critical alerts + daily reports

    Returns list of dicts: [{'phone_number', 'preferred_lang', 'display_name', 'alert_group'}]
    """
    recipients = db.query(WebhookRecipient).filter(WebhookRecipient.is_active == True).all()

    # If no recipients at all, seed default
    if not recipients:
        logger.info(f"No recipients found. Seeding default owner ({DEFAULT_OWNER_PHONE})")
        default_owner = WebhookRecipient(
            phone_number=DEFAULT_OWNER_PHONE,
            display_name="Owner",
            role="owner",
            preferred_lang="hinglish",
            subscribed_events=["*"],
            alert_group=1,
        )
        db.add(default_owner)
        db.commit()
        db.refresh(default_owner)
        recipients = [default_owner]

    matched = []
    seen_phones = set()

    for r in recipients:
        # Check if subscribed to monitoring events
        if not _is_subscribed_to_monitoring(r):
            continue

        # Check if recipient's group matches target groups
        recipient_group = getattr(r, "alert_group", 1) or 1
        if recipient_group not in target_groups:
            continue

        phone = r.phone_number
        if phone not in seen_phones:
            matched.append({
                "phone_number": phone,
                "display_name": r.display_name,
                "preferred_lang": r.preferred_lang or "hinglish",
                "alert_group": recipient_group,
            })
            seen_phones.add(phone)

    if not matched:
        logger.warning(f"No monitoring recipients found for groups {target_groups}. Check webhooks.recipients table.")

    return matched


def _is_subscribed(recipient: WebhookRecipient, event_type: str) -> bool:
    """Check if a recipient's subscribed_events matches the given event_type."""
    subs = recipient.subscribed_events or ["*"]

    for sub in subs:
        if sub == "*" or sub == event_type:
            return True
        # Wildcard matching: "tasks.*" matches "tasks.task.created"
        if sub.endswith(".*"):
            prefix = sub[:-2]
            if event_type.startswith(prefix) or event_type.split(".")[0] == prefix:
                return True

    return False


def _is_subscribed_to_monitoring(recipient: WebhookRecipient) -> bool:
    """Check if a recipient is subscribed to any monitoring events."""
    subs = recipient.subscribed_events or ["*"]

    for sub in subs:
        if sub == "*":
            return True
        if sub.startswith("monitoring"):
            return True

    return False
