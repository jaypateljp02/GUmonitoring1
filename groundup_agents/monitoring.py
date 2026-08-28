import logging
import re
from datetime import datetime, timedelta
from typing import Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text

from .base import BaseAgent
from groundup_webhooks.event_bus import emit_event

logger = logging.getLogger("groundup_agents.monitoring")


class MonitoringAgent(BaseAgent):
    """
    Floor & Compliance Monitoring Agent:
    Handles sensor telemetry queries, smart plug power draws, room maintenance states,
    and equipment issue logs for Ground Up Fermentary.
    """

    def __init__(self):
        super().__init__(
            name="Monitoring Agent",
            role_description="IoT Telemetry & Food Safety Compliance Monitor",
            system_instructions="""You are the IoT Telemetry and Floor Compliance Specialist for Ground Up Fermentary.
You report real-time temperature, humidity, power draw (Watts), and compressor runtime for all fermentation rooms, fridges, and freezers.
Always cite live numbers from the database with clear status icons (🟢 normal, 🔴 violation, ⚠️ warning)."""
        )

    async def handle(
        self,
        query: str,
        sender_phone: str,
        sender_name: str,
        parsed_intent: Dict[str, Any],
        db: Session
    ) -> str:
        lower = query.lower().strip()
        intent = parsed_intent.get("intent", "sensor_query")

        # ── 1. Maintenance Toggle ──────────────────────────────────────────
        if intent == "maintenance_update" or any(kw in lower for kw in ["cleaning fridge", "cleaning room", "maintenance", "done cleaning"]):
            room_hint = parsed_intent.get("room_or_sensor") or query
            is_starting = any(w in lower for w in ["start", "cleaning", "begin", "chalu", "lagao"]) and not any(w in lower for w in ["done", "khatam", "ho gaya", "finish"])

            room_updated = False
            room_name = "Fermentary"
            try:
                room_lower = room_hint.lower().replace("room", "").replace("fridge", "").strip()
                row = db.execute(text("""
                    SELECT id, name FROM monitoring.rooms
                    WHERE LOWER(name) LIKE :rname
                    LIMIT 1
                """), {"rname": f"%{room_lower}%"}).fetchone()

                if row:
                    db.execute(text("""
                        UPDATE monitoring.rooms
                        SET is_under_maintenance = :maint
                        WHERE id = :rid
                    """), {"maint": is_starting, "rid": row[0]})
                    db.commit()
                    room_name = row[1]
                    room_updated = True
            except Exception as me:
                logger.warning(f"Failed to update room maintenance: {me}")
                db.rollback()

            emit_event(f"monitoring.maintenance.{'started' if is_starting else 'ended'}", {
                "room_name": room_name, "reported_by": sender_name, "is_starting": is_starting
            }, actor_name=sender_name, db=db)

            if is_starting:
                return (
                    f"🔧 *Maintenance Started* ⏸️\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📍 *Area:* {room_name}\n"
                    f"👤 *By:* {sender_name}\n"
                    f"{'✅ Temperature alerts temporarily paused for this room.' if room_updated else '⚠️ Note: Room alerts logged.'}\n\n"
                    f"_Reply 'done cleaning' when finished._"
                )
            else:
                return (
                    f"✅ *Maintenance Completed* ▶️\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📍 *Area:* {room_name}\n"
                    f"👤 *By:* {sender_name}\n"
                    f"🔔 Automated monitoring alerts have resumed."
                )

        # ── 2. Floor Issue Reporting ───────────────────────────────────────
        if intent == "issue_report" or any(kw in lower for kw in ["leak", "kharaab", "not working", "broken", "awaz", "sound", "damaged"]):
            issue_desc = parsed_intent.get("issue_description") or query
            is_urgent = any(w in lower for w in ["urgent", "jaldi", "danger", "smoke", "fire", "spark"])
            
            emit_event("tasks.issue.reported", {
                "title": issue_desc, "reporter_name": sender_name,
                "severity": "high" if is_urgent else "medium"
            }, actor_name=sender_name, db=db)

            from groundup_webhooks.whatsapp_webhook import _sync_task_to_flipboard
            _sync_task_to_flipboard(f"⚠️ [ISSUE] {issue_desc}", "Owner", db)

            prio_badge = "🔴 *URGENT ISSUE*" if is_urgent else "⚠️ *Floor Issue Logged*"
            return (
                f"{prio_badge}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📝 *Details:* {issue_desc}\n"
                f"👤 *Reported by:* {sender_name}\n"
                f"⏰ *Logged:* {(datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime('%I:%M %p, %d %b')}\n\n"
                f"✅ Owner and Operations team notified on WhatsApp."
            )

        # ── 3. Live Telemetry & Sensor Query ──────────────────────────────
        try:
            # Query all active rooms with their sensors and latest telemetry
            sensors_data = db.execute(text("""
                SELECT s.id, s.name, s.type, s.device_id, s.min_threshold, s.max_threshold, r.name as room_name, s.tapo_status
                FROM monitoring.sensors s
                LEFT JOIN monitoring.rooms r ON s.room_id = r.id
                WHERE s.active = true
                ORDER BY r.name, s.name
            """)).fetchall()

            ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
            
            # Check if user asked for a specific room/equipment
            target_filter = None
            for candidate in ["miso", "vinegar", "wild", "black fridge", "hall", "terrace", "samsung", "freezer", "white fridge"]:
                if candidate in lower:
                    target_filter = candidate
                    break

            lines = [
                f"📊 *Ground Up — Live Equipment Telemetry*",
                f"⏰ *As of:* {ist_now.strftime('%I:%M %p, %d %b %Y')}",
                "━━━━━━━━━━━━━━━━━━━━"
            ]

            reported_rooms = set()
            found_count = 0

            for s in sensors_data:
                s_id, s_name, s_type, dev_id, min_t, max_t, room_name, tapo_st = s
                r_name = room_name or s_name

                if target_filter and target_filter not in r_name.lower() and target_filter not in s_name.lower():
                    continue

                if s_type == "plug":
                    tel = db.execute(text("""
                        SELECT apower, voltage, current, timestamp 
                        FROM monitoring.plug_telemetry 
                        WHERE device_id = :did 
                        ORDER BY timestamp DESC LIMIT 1
                    """), {"did": dev_id}).fetchone()
                    
                    p_val = f"{tel[0]:.1f}W" if tel and tel[0] is not None else "0.0W"
                    v_val = f"{tel[1]:.0f}V" if tel and tel[1] is not None else "230V"
                    status_icon = "🟢" if tel and (datetime.utcnow() - tel[3]).total_seconds() < 3600 else "⚠️"
                    lines.append(f"{status_icon} *{s_name} (Plug):* {p_val} ({v_val})")
                    found_count += 1

                elif s_type == "temperature":
                    tel = db.execute(text("""
                        SELECT temperature, humidity, battery_level, timestamp 
                        FROM monitoring.device_telemetry 
                        WHERE device_id = :did 
                        ORDER BY timestamp DESC LIMIT 1
                    """), {"did": dev_id}).fetchone()

                    if tel and tel[0] is not None:
                        t_val = tel[0]
                        h_val = tel[1]
                        batt = tel[2]
                        
                        is_violating = False
                        if max_t is not None and t_val > float(max_t):
                            is_violating = True
                            
                        t_icon = "🔴" if is_violating else "🟢"
                        hum_str = f" | {h_val:.0f}% RH" if h_val is not None else ""
                        lines.append(f"{t_icon} *{s_name}:* *{t_val:.1f}°C*{hum_str} (Batt: {batt:.0f}%)")
                        found_count += 1

            if found_count == 0:
                lines.append("ℹ️ No active sensors matched your filter. Available areas: Miso Room, Vinegar Room, Wild Room, Black Fridge, Hall Fridge/Freezer, Terrace Fridge, Samsung Fridge.")

            lines.append("\n_All sensor telemetry synced via eWeLink & Tapo._")
            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Error querying telemetry in MonitoringAgent: {e}")
            return "⚠️ Error retrieving live sensor telemetry. Please try again."
