"""
Message Formatter for groundup-webhooks.
Converts structured event payloads into clean, human-readable WhatsApp messages in Hinglish/Hindi/English.
"""
import logging
from typing import Dict, Any

logger = logging.getLogger("groundup_webhooks.message_formatter")

def format_event_message(event_type: str, payload: Dict[str, Any], lang: str = "hinglish") -> str:
    """Format an event into a clean human-readable WhatsApp message string."""

    # 1. Monitoring Alert Triggered
    if event_type == "monitoring.alert.triggered":
        sensor = payload.get("sensor_name", "Equipment")
        current_val = payload.get("current_value", "N/A")
        normal = payload.get("normal_range", "Normal")
        duration = payload.get("duration", "N/A")
        priority = payload.get("priority", "High")
        prio_icon = "🔴" if priority.lower() in ("critical", "high") else "🟡"

        return (
            f"🚨 *Temperature Alert* {prio_icon}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 *Sensor:* {sensor}\n"
            f"🌡️ *Reading:* {current_val}\n"
            f"📏 *Normal:* {normal}\n"
            f"⏱️ *Duration:* {duration}\n"
            f"⚠️ *Priority:* {priority}\n\n"
            f"_Reply 'cleaning {sensor}' if maintenance started._"
        )

    # 2. Monitoring Alert Recovered
    elif event_type == "monitoring.alert.recovered":
        sensor = payload.get("sensor_name", "Equipment")
        current_val = payload.get("current_value", "Normal")
        return (
            f"✅ *Temperature Recovered*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 *Sensor:* {sensor}\n"
            f"🌡️ *Current Reading:* {current_val}\n"
            f" Status: Back to Normal range."
        )

    # 3. Monitoring Offline
    elif event_type == "monitoring.sensor.offline":
        sensor = payload.get("sensor_name", "Sensors")
        duration = payload.get("duration", "15+ mins")
        return (
            f"⚠️ *Equipment Offline Alert*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 *Device:* {sensor}\n"
            f"📡 *Status:* OFFLINE ({duration})\n"
            f"Please check power & internet connection."
        )

    # 4. Task Created / Assigned
    elif event_type in ("tasks.task.created", "flipboard.item.created"):
        title = payload.get("text", payload.get("title", "New Task"))
        assignee = payload.get("assigned_to_name", payload.get("assigned_to", "Worker"))
        creator = payload.get("created_by", "Owner")
        priority = payload.get("priority", "normal")
        prio_icon = "🔴" if priority == "urgent" else "🟡" if priority == "normal" else "🟢"

        return (
            f"📋 *Naya Kaam (New Task)* {prio_icon}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 *Assigned To:* {assignee}\n"
            f"📝 *Task:* {title}\n"
            f"👔 *Given By:* {creator}\n\n"
            f"_Reply 'done' when completed, or send a voice note._"
        )

    # 5. Task Completed
    elif event_type in ("tasks.task.completed", "flipboard.item.completed"):
        title = payload.get("text", payload.get("title", "Task"))
        completed_by = payload.get("completed_by", payload.get("assigned_to_name", "Worker"))
        duration = payload.get("duration", "")
        duration_str = f"\n⏱️ *Duration:* {duration}" if duration else ""

        return (
            f"✅ *Kaam Poora Ho Gaya (Task Done)*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 *Completed By:* {completed_by}\n"
            f"📝 *Task:* {title}{duration_str}"
        )

    # 6. Issue Reported
    elif event_type == "tasks.issue.reported":
        title = payload.get("title", payload.get("text", "Issue"))
        reporter = payload.get("reporter_name", "Employee")
        severity = payload.get("severity", "medium")
        sev_icon = "🔴" if severity == "high" else "🟡"

        return (
            f"⚠️ *Floor Issue Reported* {sev_icon}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 *Reported By:* {reporter}\n"
            f"🛠️ *Issue:* {title}\n"
            f"⚡ *Severity:* {severity.upper()}"
        )

    # 7. Production Jar Quality Alert
    elif event_type == "production.jar.quality_alert":
        jar_num = payload.get("jar_number", "N/A")
        smell = payload.get("smell", "N/A")
        taste = payload.get("taste", "N/A")
        recorder = payload.get("recorded_by", "Worker")
        notes = payload.get("observation", payload.get("notes", ""))

        return (
            f"🚨 *JAR QUALITY ALERT* 🔴\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 *Jar #:* {jar_num}\n"
            f"👃 *Smell:* {smell}\n"
            f"👅 *Taste:* {taste}\n"
            f"👤 *Checked By:* {recorder}\n"
            f"📝 *Notes:* {notes}\n\n"
            f"_Immediate inspection recommended._"
        )

    # 8. Employee Question Asked
    elif event_type == "admin.question.asked":
        question = payload.get("question", payload.get("title", "Question"))
        asked_by = payload.get("asked_by", "Employee")
        product = payload.get("product_name", "")
        prod_str = f"\n📦 *Product:* {product}" if product else ""

        return (
            f"❓ *Employee Question Alert*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 *From:* {asked_by}{prod_str}\n"
            f"💬 *Question:* {question}\n\n"
            f"_Reply on WhatsApp or check Admin Panel._"
        )

    # 9. Meeting Action Items Approved by Owner
    elif event_type == "meetings.action_items.approved":
        assignee = payload.get("assignee", "Worker")
        task = payload.get("task", "Action Item")
        due = payload.get("due", "")
        meeting_title = payload.get("meeting_title", "Last Meeting")
        due_str = f"\n⏰ *Due Date:* {due}" if due else ""

        return (
            f"📌 *Meeting Action Item Assigned* ✅\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 *Assigned To:* {assignee}\n"
            f"📝 *Task:* {task}{due_str}\n"
            f"🎙️ *From Meeting:* {meeting_title}\n\n"
            f"_Approved by Owner. Reply 'done' when completed._"
        )

    # 10. Supply Requested
    elif event_type == "tasks.supply.requested":
        item = payload.get("item", payload.get("title", "Supply"))
        requested_by = payload.get("requested_by", "Employee")
        severity = payload.get("severity", "normal")
        sev_icon = "🔴" if severity == "high" else "🛒"

        return (
            f"{sev_icon} *Supply / Material Needed*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 *Requested By:* {requested_by}\n"
            f"📦 *Item Needed:* {item}\n"
            f"⚡ *Urgency:* {severity.upper()}"
        )

    # 11. Production Batch Logged
    elif event_type == "production.batch.logged":
        product = payload.get("product", "Batch")
        quantity = payload.get("quantity", "")
        logged_by = payload.get("logged_by", "Worker")
        qty_str = f"\n⚖️ *Quantity:* {quantity}" if quantity else ""

        return (
            f"🏭 *Production Batch Logged*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 *Logged By:* {logged_by}\n"
            f"📦 *Product:* {product}{qty_str}"
        )

    # Fallback / Generic Event
    else:
        return (
            f"📢 *Factory Notification: {event_type}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{payload}"
        )
