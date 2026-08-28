import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text

from .base import BaseAgent
from groundup_webhooks.event_bus import emit_event

logger = logging.getLogger("groundup_agents.production")


class ProductionAgent(BaseAgent):
    """
    Production & Batch Lifecycle Agent:
    Manages fermentation batches, packaging into jars, quality check scoring (taste/smell/color),
    supply requests, and jar timelines.
    """

    def __init__(self):
        super().__init__(
            name="Production Agent",
            role_description="Fermentary Batch & Quality Assurance Manager",
            system_instructions="""You manage batch packaging, quality scoring (taste/smell/color/aroma/umami),
jar timelines, and supply requisitions for Ground Up Fermentary.
Ensure every batch has clear packaging details (material, capacity, recipe, date) and prompt for photos when needed."""
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
        intent = parsed_intent.get("intent", "production_log")

        # ── 1. Supply / Inventory Requisition ──────────────────────────────
        if intent == "supply_request" or any(kw in lower for kw in ["khatam", "order karo", "supply", "chahiye", "need more", "out of stock"]):
            item_desc = parsed_intent.get("issue_description") or parsed_intent.get("task_hint") or query
            # Clean common prefixes
            item_desc = re.sub(r"^(need more|chahiye|order karo|supply|khatam ho gaya)\s*", "", item_desc, flags=re.IGNORECASE).strip()
            
            emit_event("tasks.supply.requested", {
                "title": f"Supply needed: {item_desc}",
                "requested_by": sender_name,
                "item": item_desc,
                "severity": "high" if "urgent" in lower else "normal"
            }, actor_name=sender_name, db=db)

            # Sync to today's FlipBoard
            from groundup_webhooks.whatsapp_webhook import _sync_task_to_flipboard
            _sync_task_to_flipboard(f"🛒 Supply: {item_desc}", "Owner", db)

            return (
                f"📦 *Supply Request Logged* ✅\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🛒 *Item:* {item_desc}\n"
                f"👤 *Requested By:* {sender_name}\n"
                f"📋 Added to today's FlipBoard and notified Owner."
            )

        # ── 2. Production Batch Packaging / Assigning ─────────────────────
        jar_match = re.search(r"jar\s*#?\s*(\d+)", lower)
        jar_num = int(jar_match.group(1)) if jar_match else parsed_intent.get("jar_number")

        is_packaging = any(w in lower for w in ["package", "pack ", "assign recipe", "scale batch", "started batch"])

        if is_packaging and jar_num:
            mat = "Glass" if any(w in lower for w in ["glass", "kanch", "sheesha"]) else "Plastic"
            cap_match = re.search(r"(\d+)\s*(l|litre|liter|kg)?", lower)
            cap = int(cap_match.group(1)) if cap_match else (20 if mat == "Glass" else 50)
            jt_label = f"{mat} ({cap}L)"

            rec_name = "White Miso" if "white" in lower else "Red Miso" if "red" in lower else "Rice Koji" if "koji" in lower else "Miso Ferment"

            try:
                now_dt = datetime.utcnow()
                ready_dt = now_dt + timedelta(days=90)
                jar_url = f"https://gu-production.initiativesewafoundation.com/jar.html?jar={jar_num}"

                db.execute(text("""
                    INSERT INTO production.jar_timeline (id, jar_id, action, details, recorded_by, created_at)
                    SELECT :eid, j.id, 'packaged', CAST(:details AS jsonb), NULL, NOW()
                    FROM production.jars j WHERE j.jar_number = :jnum
                """), {
                    "eid": str(uuid.uuid4()),
                    "jnum": jar_num,
                    "details": json.dumps({
                        "batch_size": cap,
                        "material": mat,
                        "capacity_litres": cap,
                        "jar_type": jt_label,
                        "notes": f"Recipe: {rec_name}. Container: {jt_label}. Packaged by {sender_name}.",
                        "recorded_by": sender_name,
                        "timestamp": now_dt.isoformat(),
                        "target_ready_date": ready_dt.isoformat(),
                        "fermentation_days": 90
                    })
                })
                db.commit()

                j_icon = "🏺" if mat == "Glass" else "🪣"
                return (
                    f"🎉 *Jar #{jar_num} Batch Packaged!* 📦\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🍲 *Product:* {rec_name}\n"
                    f"{j_icon} *Container:* {jt_label}\n"
                    f"⚖️ *Quantity:* {cap} kg\n"
                    f"👤 *Assigned by:* {sender_name}\n"
                    f"⏳ *Target Ready:* {(now_dt + timedelta(days=90)).strftime('%d %b %Y')}\n\n"
                    f"_View live details: {jar_url}_"
                )
            except Exception as pe:
                logger.error(f"Error packaging batch via ProductionAgent: {pe}")
                db.rollback()

        # ── 3. Jar Status Query or Quality Check Submission ────────────────
        if jar_num:
            jar_url = f"https://gu-production.initiativesewafoundation.com/jar.html?jar={jar_num}"
            jar_row = db.execute(text("SELECT id, jar_number, status, created_at FROM production.jars WHERE jar_number = :jnum"), {"jnum": jar_num}).fetchone()
            timeline_rows = []
            if jar_row:
                timeline_rows = db.execute(text("SELECT action, details, created_at FROM production.jar_timeline WHERE jar_id = :jid ORDER BY created_at DESC"), {"jid": str(jar_row[0])}).fetchall()

            scores = parsed_intent.get("quality_scores", {}) or {}
            score_matches = re.findall(r"(taste|smell|color|umami|sweetness|aroma)\s*(\d+)", lower)
            for attr, val in score_matches:
                scores[attr] = int(val)

            has_scores = bool(scores)
            has_observation = any(w in lower for w in ["mold", "bad", "foul", "sour", "bitter", "good", "sahi", "theek", "kharaab", "thik", "normal", "badhiya"]) and not (lower.startswith("check jar") and len(lower.split()) <= 3)

            if not timeline_rows:
                return (
                    f"🫙 *Jar #{jar_num} is UNASSIGNED (Clean Jar)* ✨\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📦 *Status:* Clean & Empty (Available for batch)\n"
                    f"👤 *Checked by:* {sender_name}\n\n"
                    f"📝 *To package a new recipe batch into Jar #{jar_num}:*\n"
                    f"👉 {jar_url}\n"
                    f"Or reply: *\"package 50kg red miso into jar {jar_num}\"*"
                )

            # Active Jar: User is only asking status
            if not (has_scores or has_observation):
                pack_event = next((e for e in timeline_rows if e[0] in ('packaged', 'package')), timeline_rows[-1])
                prod_name = "Miso Ferment"
                batch_size = "50"
                pack_dt_str = "Recently"
                ready_dt_str = "In 90 days"
                age_days = 1
                jar_type_str = ""

                if pack_event and pack_event[1]:
                    details = pack_event[1] if isinstance(pack_event[1], dict) else json.loads(pack_event[1])
                    batch_size = details.get("batch_size", "50")
                    if details.get("notes") and "Recipe:" in details["notes"]:
                        prod_name = details["notes"].split("Recipe:")[1].split(".")[0].strip()
                    jt = details.get("jar_type")
                    if jt:
                        j_icon = "🏺" if "glass" in jt.lower() else "🪣"
                        jar_type_str = f"\n{j_icon} *Container:* {jt}"

                    if pack_event[2]:
                        now = datetime.utcnow()
                        pack_created = pack_event[2]
                        age_days = max(1, (now - pack_created).days + 1)
                        pack_dt_str = pack_created.strftime("%d %b %Y")
                        ferment_cycle = details.get("fermentation_days", 90)
                        ready_dt = pack_created + timedelta(days=ferment_cycle)
                        ready_dt_str = ready_dt.strftime("%d %b %Y")

                return (
                    f"🫙 *Jar #{jar_num} — Active Batch Status* 🔍\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🍲 *Product:* {prod_name} (Day {age_days})\n"
                    f"📦 *Batch Size:* {batch_size} kg{jar_type_str}\n"
                    f"📅 *Packaged:* {pack_dt_str} | ⏳ *Target Ready:* {ready_dt_str}\n"
                    f"👤 *Inspected by:* {sender_name}\n\n"
                    f"📝 *Record Inspection:*\n"
                    f"• *\"jar {jar_num} taste 8 smell 9 color 8\"*\n"
                    f"• Or send a 📷 photo of the ferment for AI check!\n\n"
                    f"_Full timeline: {jar_url}_"
                )

            # User is submitting actual scores / observations
            qual = parsed_intent.get("quality_details", {}) or {}
            smell = qual.get("smell") or ("off" if any(w in lower for w in ["mold", "bad", "foul", "kharaab"]) else "good" if any(w in lower for w in ["good", "sahi", "theek"]) else "normal")
            taste = qual.get("taste") or ("off" if any(w in lower for w in ["sour", "bad", "bitter", "kharaab"]) else "normal")
            color = qual.get("color", "normal")
            is_bad = any(w in lower for w in ["mold", "foul", "bad", "kharaab"])

            try:
                db.execute(text("""
                    INSERT INTO production.jar_timeline (id, jar_id, action, details, recorded_by, created_at)
                    SELECT :eid, j.id, 'quality_check', CAST(:details AS jsonb), NULL, NOW()
                    FROM production.jars j WHERE j.jar_number = :jnum
                """), {
                    "eid": str(uuid.uuid4()), "jnum": jar_num,
                    "details": json.dumps({"smell": smell, "taste": taste, "color": color, "scores": scores, "is_alert": is_bad, "notes": query, "recorded_by": sender_name}),
                })
                db.commit()
            except Exception as je:
                logger.warning(f"Could not save jar QC event: {je}")
                db.rollback()

            ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
            current_time_str = ist_now.strftime("%I:%M %p, %d %b %Y")

            if is_bad:
                emit_event("production.jar.quality_alert", {
                    "jar_number": jar_num, "smell": smell, "taste": taste, "color": color,
                    "recorded_by": sender_name, "observation": query, "scores": scores
                }, actor_name=sender_name, db=db)
                return (
                    f"🚨 *JAR QUALITY ALERT* 🔴\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📦 *Jar #:* {jar_num}\n"
                    f"👃 *Smell:* {smell} | 👅 *Taste:* {taste} | 🎨 *Color:* {color}\n"
                    f"👤 *Checked By:* {sender_name}\n"
                    f"⏰ *Time:* {current_time_str}\n"
                    f"📝 *Notes:* {query[:100]}\n\n"
                    f"⚠️ Owner and Chef Gaya notified!\n"
                    f"_Full timeline: {jar_url}_"
                )
            else:
                score_line = f"\n📊 *Scores:* {' | '.join([f'{k.title()}: {v}/10' for k, v in scores.items() if v])}" if scores else ""
                return (
                    f"🫙 *Jar #{jar_num} Quality Check Recorded* ✅\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"👃 Smell: {smell} | 👅 Taste: {taste} | 🎨 Color: {color}{score_line}\n"
                    f"👤 Checked By: {sender_name}\n"
                    f"⏰ Time: {current_time_str}\n\n"
                    f"_Full timeline: {jar_url}_"
                )

        # ── 4. General Production Batch Logging ────────────────────────────
        product = parsed_intent.get("product_name") or query
        product = re.sub(r"^(batch|production|packed|started)\s*", "", product, flags=re.IGNORECASE).strip()
        quantity = parsed_intent.get("quantity", "")

        from groundup_webhooks.whatsapp_webhook import _sync_task_to_flipboard
        _sync_task_to_flipboard(f"🏭 {product}" + (f" ({quantity})" if quantity else ""), sender_name, db)

        emit_event("production.batch.logged", {
            "product": product, "quantity": quantity,
            "logged_by": sender_name, "raw_message": query
        }, actor_name=sender_name, db=db)

        return (
            f"🏭 *Production Logged* ✅\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 *Product:* {product}\n"
            + (f"⚖️ *Quantity:* {quantity}\n" if quantity else "")
            + f"👤 *Logged By:* {sender_name}\n"
            f"📋 Added to FlipBoard & registered in production logs."
        )
