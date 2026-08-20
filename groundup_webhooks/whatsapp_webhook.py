"""
Central WhatsApp Webhook Handler & Multi-Lingual AI Router for Ground Up.

Handles:
- Verification (GET /webhook/whatsapp) with verify_token 'initiative2026'
- Incoming WhatsApp messages (POST /webhook/whatsapp):
  * Text messages (Hindi, Hinglish, Marathi, Bengali, English)
  * Voice Notes (Ogg/Opus audio transcription via Gemini Flash)
  * Photos & QR Images (factory-aware Gemini Vision analysis + forwarding to owner)
- Multi-Intent AI Router:
  * Task Completion ("done 3", "pack kardia 50 jars", "miso room saaf ho gaya")
  * Task Creation & Assignment ("add Clean fridge - Ravi", "Tell Yadav to check vinegar room")
  * Maintenance Start/End with real DB update ("cleaning fridge 1", "done cleaning")
  * Floor Issue Reporting ("motor leak kar raha hai")
  * Call Confirmation (YES / SKIP)
  * Jar QR Quality Check with scoring & timeline link ("jar 42 taste 8 smell 9 color 7")
  * Status & Dashboard with role-based output (Owner sees factory, Employee sees their tasks)
  * Recipe Queries ("miso recipe", "salt kitna daalna hai", "vinegar ingredients")
  * Supply/Inventory Requests ("pH strips khatam", "need more salt")
  * Production Batch Logging ("started miso batch 5", "packed 50 jars")
  * Meeting Intelligence ("what did we decide about salt", "my action items")
- Smart Features:
  * Auto role-detection from phone number (Owner/Admin/Employee)
  * Photo analysis forwarded to owner automatically
  * Carry-over pending tasks from previous days
  * Factory-aware Gemini Vision (miso, vinegar, koji, fermentation)
"""
import os
import json
import logging
import base64
import re
from datetime import datetime, timedelta
import httpx
from fastapi import APIRouter, Request, Response, Depends, BackgroundTasks, HTTPException
from sqlalchemy.orm import Session

from groundup_webhooks.config import (
    WHATSAPP_VERIFY_TOKEN,
    WHATSAPP_ACCESS_TOKEN,
    GEMINI_API_KEY
)
from groundup_webhooks.database import get_db, SessionLocal
from groundup_webhooks.models import WhatsAppMessage, WebhookRecipient
from groundup_webhooks.whatsapp_client import send_whatsapp_text
from groundup_webhooks.event_bus import emit_event

logger = logging.getLogger("groundup_webhooks.whatsapp_webhook")

router = APIRouter(prefix="/webhook/whatsapp", tags=["WhatsApp Unified Webhook"])


@router.get("")
async def verify_webhook(request: Request):
    """Meta WhatsApp Webhook Verification Endpoint."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    valid_tokens = {WHATSAPP_VERIFY_TOKEN, "gubandhu2026", "initiative2026"}
    if mode == "subscribe" and token in valid_tokens:
        logger.info("WhatsApp webhook verified successfully.")
        return Response(content=challenge, media_type="text/plain")


    logger.warning(f"WhatsApp webhook verification failed. Token received: {token}")
    return Response(content="Verification failed", status_code=403)


@router.post("")
async def handle_incoming_message(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Main Webhook Payload Entry Point from Meta Cloud API."""
    try:
        body = await request.json()
        if isinstance(body, str):
            body = json.loads(body)
    except Exception:
        return Response(status_code=400)

    try:
        if not isinstance(body, dict):
            return {"status": "ignored"}

        entries = body.get("entry", [])
        if not entries or not isinstance(entries, list):
            return {"status": "ignored"}

        entry = entries[0]
        changes = entry.get("changes", [])
        if not changes or not isinstance(changes, list):
            return {"status": "ignored"}

        value = changes[0].get("value", {})
        messages = value.get("messages", [])
        contacts = value.get("contacts", [])
        statuses = value.get("statuses", [])

        # Handle Meta Delivery Status Receipts (sent, delivered, read, failed)
        if statuses and isinstance(statuses, list):
            st = statuses[0]
            recipient_id = st.get("recipient_id", "")
            status = st.get("status", "")
            errors = st.get("errors", [])
            err_details = errors[0].get("title", "") if errors else ""
            
            if status == "failed":
                logger.error(f"❌ Meta Delivery FAILED for {recipient_id}: {err_details}")
                msg_record = WhatsAppMessage(
                    direction="status",
                    phone_number=recipient_id,
                    sender_name="Meta Webhook",
                    message_type="status",
                    raw_content=f"Status: failed | Error: {err_details}",
                    status="failed"
                )
                db.add(msg_record)
                db.commit()
            return {"status": "success"}

        if not messages or not isinstance(messages, list):
            return {"status": "ignored"}

        message = messages[0]
        sender_phone = message.get("from", "")
        sender_name = contacts[0].get("profile", {}).get("name", "Unknown") if contacts else "Unknown"
        msg_type = message.get("type", "text")
        meta_msg_id = message.get("id", "")

        logger.info(f"📩 Incoming WhatsApp from {sender_phone} ({sender_name}) | type={msg_type}")

        raw_content = ""
        media_url = None

        # 1. Handle Text Messages
        if msg_type == "text":
            raw_content = message.get("text", {}).get("body", "").strip()

        # 2. Handle Voice Notes (Audio)
        elif msg_type == "audio":
            audio_id = message.get("audio", {}).get("id")
            if audio_id:
                raw_content = await _download_and_transcribe_audio(audio_id)

        # 3. Handle Images / Photos
        elif msg_type == "image":
            image_id = message.get("image", {}).get("id")
            caption = message.get("image", {}).get("caption", "")
            if image_id:
                raw_content = await _analyze_image(image_id, caption)

        # 4. Handle Button Clicks & Interactive Responses from Meta Templates
        elif msg_type == "button":
            raw_content = message.get("button", {}).get("text", "") or message.get("button", {}).get("payload", "")
        elif msg_type == "interactive":
            interactive = message.get("interactive", {})
            itype = interactive.get("type")
            if itype == "button_reply":
                raw_content = interactive.get("button_reply", {}).get("title", "") or interactive.get("button_reply", {}).get("id", "")
            elif itype == "list_reply":
                raw_content = interactive.get("list_reply", {}).get("title", "")

        if not raw_content:
            raw_content = f"[{msg_type.upper()} message]"

        # Log incoming message to DB
        msg_record = WhatsAppMessage(
            direction="incoming",
            phone_number=sender_phone,
            sender_name=sender_name,
            message_type=msg_type,
            raw_content=raw_content,
            status="received"
        )
        db.add(msg_record)
        db.commit()

        # 4. Multi-Lingual Intent Parsing & AI Routing via Threadpool Background Worker
        is_photo = msg_type == "image"

        def _process_in_background():
            import asyncio
            from groundup_webhooks.database import SessionLocal
            bg_db = SessionLocal()
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    # For photos: extract analysis and send formatted reply + forward to owner
                    if is_photo and "[PHOTO_ANALYSIS]" in raw_content:
                        analysis = raw_content.split("[PHOTO_ANALYSIS]")[1].split("[/PHOTO_ANALYSIS]")[0].strip()
                        caption_part = raw_content.split("[/PHOTO_ANALYSIS]")[-1].strip() if "[/PHOTO_ANALYSIS]" in raw_content else ""

                        # Send formatted reply to sender
                        photo_reply = (
                            f"📸 *Photo Analysis*\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"{analysis}\n"
                        )
                        if caption_part:
                            photo_reply += f"\n📝 *Your caption:* {caption_part}"
                        loop.run_until_complete(send_whatsapp_text(sender_phone, photo_reply, bg_db, sender_name="GroundUp Bot"))

                        # Forward photo analysis to owner (so owner always knows what's happening)
                        try:
                            sender_info = _get_sender_info(sender_phone, bg_db)
                            if sender_info["role"] not in ("owner", "admin"):
                                owner_recipients = bg_db.query(WebhookRecipient).filter(
                                    WebhookRecipient.is_active == True,
                                    WebhookRecipient.role == "owner"
                                ).all()
                                for owner in owner_recipients:
                                    owner_msg = (
                                        f"📸 *Photo from {sender_name}*\n"
                                        f"━━━━━━━━━━━━━━━━━━━━\n"
                                        f"{analysis}\n"
                                    )
                                    if caption_part:
                                        owner_msg += f"\n📝 *Caption:* {caption_part}"
                                    loop.run_until_complete(send_whatsapp_text(
                                        owner.phone_number, owner_msg, bg_db, sender_name="GroundUp Bot"
                                    ))
                        except Exception as fwd_err:
                            logger.warning(f"Failed to forward photo to owner: {fwd_err}")

                        # Also run through intent parser in case caption has actionable content
                        if caption_part and len(caption_part) > 3:
                            try:
                                parsed = loop.run_until_complete(asyncio.wait_for(
                                    _parse_intent_with_gemini(caption_part, sender_phone, sender_name, bg_db), timeout=4.0
                                ))
                                intent = parsed.get("intent", "general_chat")
                                if intent not in ("general_chat", "unknown"):
                                    reply_text = loop.run_until_complete(_execute_parsed_intent(parsed, sender_phone, sender_name, caption_part, bg_db))
                                    if reply_text:
                                        loop.run_until_complete(send_whatsapp_text(sender_phone, reply_text, bg_db, sender_name="GroundUp Bot"))
                            except Exception:
                                pass
                    else:
                        # Normal text/voice/button flow
                        try:
                            parsed = loop.run_until_complete(asyncio.wait_for(_parse_intent_with_gemini(raw_content, sender_phone, sender_name, bg_db), timeout=4.0))
                        except Exception as ge:
                            logger.warning(f"Gemini intent parsing timeout/error: {ge}, using fallback keyword parser")
                            parsed = {"intent": "task_create", "task_hint": raw_content}

                        reply_text = loop.run_until_complete(_execute_parsed_intent(parsed, sender_phone, sender_name, raw_content, bg_db))
                        if reply_text:
                            loop.run_until_complete(send_whatsapp_text(sender_phone, reply_text, bg_db, sender_name="GroundUp Bot"))
                finally:
                    loop.close()
            except Exception as bg_err:
                logger.error(f"Background intent execution error: {bg_err}", exc_info=True)
            finally:
                bg_db.close()

        background_tasks.add_task(_process_in_background)
        return {"status": "success"}

    except Exception as e:
        logger.error(f"Error handling WhatsApp webhook: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


async def _parse_intent_with_gemini(text: str, phone: str, sender_name: str, db: Session) -> dict:
    """Uses Gemini 2.0 Flash to parse multi-lingual messages into structured intent JSON."""
    if not GEMINI_API_KEY:
        # Fallback simple keyword match
        lower = text.lower()
        if "done" in lower or "ho gaya" in lower or "saaf" in lower:
            return {"intent": "task_complete", "details": {"task_hint": text}}
        elif lower == "yes" or lower == "y":
            return {"intent": "call_confirm_yes"}
        elif lower == "skip" or lower == "no":
            return {"intent": "call_confirm_skip"}
        elif lower in ("status", "pending"):
            return {"intent": "status_query"}
        return {"intent": "general_chat", "text": text}

    prompt = f"""You are the AI Factory Assistant for Ground Up Factory in Pune, India.
    An employee or owner sent this message on WhatsApp: "{text}"
    Sender Phone: {phone}, Name: {sender_name}

    Understand the message in any language (Hindi, Hinglish, Marathi, Bengali, English).
    Categorize into ONE of these intents:

    1. "task_complete" — user says a task/cleaning/packing is finished (e.g. "done 3", "pack kardia 50 jars", "miso room saaf ho gaya", "kaam ho gaya").
    2. "task_create" — user wants to add/assign a task to someone or themselves (e.g. "Tell Yadav to check vinegar room", "assign Ravi clean fridge", "add Clean fridge - Ravi", "naya kaam karo").
    3. "maintenance_update" — user starting/ending fridge cleaning (e.g. "cleaning fridge 1", "defrosting start", "done cleaning fridge 1").
    4. "issue_report" — reporting a machine problem or floor issue (e.g. "motor leak kar raha hai").
    5. "jar_check" — jar quality check or QR scan (e.g. "check jar 42", "jar 42 smell good taste normal", "jar 42 taste 8 smell 9 color 7").
    6. "status_query" — asking for today's tasks or status (e.g. "status", "pending", "aaj ke kaam").
    7. "call_confirm_yes" — replying YES to confirm call recording tasks.
    8. "call_confirm_skip" — replying SKIP/NO to call recording tasks.
    9. "recipe_query" — asking for recipe weights, ingredients, or how to make something (e.g. "miso recipe", "salt kitna daalna hai", "white miso banane ka tarika", "vinegar ingredients").
    10. "meeting_query" — asking about a past meeting or what was decided (e.g. "what did we decide about salt", "last meeting summary", "kya decide hua").
    11. "meeting_action_items" — asking for pending action items from meetings (e.g. "my action items", "meeting tasks", "kya karna hai").
    12. "supply_request" — reporting supplies running low or requesting materials (e.g. "pH strips khatam ho gaye", "need more salt", "jars chahiye", "order karo").
    13. "production_log" — logging production/batch work (e.g. "started miso batch 5", "packed 50 jars today", "vinegar batch ready").
    14. "product_query" — asking about factory products, catalog, or available items (e.g. "show products", "miso list", "product catalog", "kya products hain").
    15. "report_query" — asking for factory report summary, daily report, or task reports (e.g. "today's report", "weekly report", "summary report", "report bhejo").
    16. "sensor_query" — asking for detailed sensor history, temperature log, or offline status (e.g. "fridge 1 temp history", "temperature report", "sensor log", "offline sensors").
    17. "general_chat" — general talk or unknown.

    Return ONLY a JSON object:
    {{
        "intent": "...",
        "language_detected": "hindi/hinglish/marathi/bengali/english",
        "action": "start/end/complete/create/query",
        "task_hint": "...",
        "assigned_to": "name of target person if mentioned, e.g. Yadav Sir / Ravi / Priya / Owner",
        "room_or_sensor": "...",
        "jar_number": null or integer,
        "quality_details": {{"smell": "...", "taste": "...", "color": "...", "texture": "..."}},
        "quality_scores": {{"taste": null, "smell": null, "color": null, "umami": null, "sweetness": null, "aroma": null}},
        "issue_description": "...",
        "product_name": "...",
        "quantity": "...",
        "reply_in_language": "suggested polite reply in Hinglish/Hindi/English"
    }}"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "response_mime_type": "application/json"}
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, json=payload)
            if res.status_code == 200:
                result = res.json()
                content_text = result["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(content_text.strip())
    except Exception as e:
        logger.warning(f"Gemini intent parsing failed: {e}")

    # Explicit fallback for status queries if Gemini fails or returns something generic
    lower_text = text.lower()
    if "pending" in lower_text or "status" in lower_text or "tasks" in lower_text:
        return {"intent": "status_query", "text": text}

    return {"intent": "general_chat", "text": text}


def _resolve_dynamic_assigned_name(raw_text: str, ai_assigned: str, db: Session) -> str:
    """Dynamically matches recipient display_name from DB against text content."""
    try:
        recipients = db.query(WebhookRecipient).filter(WebhookRecipient.is_active == True).all()
        lower_text = raw_text.lower()
        
        for r in recipients:
            dname = r.display_name
            first_name = dname.split()[0].lower()
            if len(first_name) >= 3 and (first_name in lower_text or (ai_assigned and first_name in ai_assigned.lower())):
                return dname

        if "yadhav" in lower_text or "yadav" in lower_text:
            for r in recipients:
                if "yadav" in r.display_name.lower():
                    return r.display_name
            return "Yadav Sir"
    except Exception:
        pass

    return ai_assigned or "Staff"


def _get_sender_info(phone: str, db: Session) -> dict:
    """Auto-detect sender's role, name, and language from their phone number in webhooks.recipients.
    This is the single source of truth — no asking 'who are you', the DB knows."""
    try:
        clean = phone.replace("+", "").replace(" ", "").strip()
        recipient = db.query(WebhookRecipient).filter(
            WebhookRecipient.is_active == True
        ).all()
        for r in recipient:
            db_phone = r.phone_number.replace("+", "").replace(" ", "").strip()
            if db_phone == clean or clean.endswith(db_phone) or db_phone.endswith(clean):
                return {
                    "role": r.role or "employee",
                    "display_name": r.display_name,
                    "preferred_lang": r.preferred_lang or "hinglish",
                    "alert_group": getattr(r, "alert_group", 1) or 1,
                }
    except Exception:
        pass
    return {"role": "employee", "display_name": None, "preferred_lang": "hinglish", "alert_group": 1}


def _sync_task_to_flipboard(title: str, assigned_to: str, db: Session):
    """Auto-creates item on FlipBoard on Page 1 of TODAY'S active Daily Work folder."""
    try:
        import uuid
        from datetime import datetime, timedelta
        from sqlalchemy import text
        
        today_str = (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
        
        folder_res = db.execute(text("""
            SELECT id FROM flipboard.folders
            WHERE folder_type = 'daily_auto' AND title LIKE :tpattern
            ORDER BY created_at DESC LIMIT 1
        """), {"tpattern": f"Daily Work - {today_str}%"}).fetchone()
        
        if not folder_res:
            fid = str(uuid.uuid4())
            db.execute(text("""
                INSERT INTO flipboard.folders (id, title, folder_type, created_at)
                VALUES (:fid, :title, 'daily_auto', NOW())
            """), {"fid": fid, "title": f"Daily Work - {today_str}"})
            db.commit()
            folder_id = fid
        else:
            folder_id = folder_res[0]

        page_res = db.execute(text("""
            SELECT id FROM flipboard.pages
            WHERE folder_id = :fid AND page_number = 1
            LIMIT 1
        """), {"fid": folder_id}).fetchone()

        if not page_res:
            pid = str(uuid.uuid4())
            db.execute(text("""
                INSERT INTO flipboard.pages (id, folder_id, page_number, page_date, created_at)
                VALUES (:pid, :fid, 1, CURRENT_DATE, NOW())
            """), {"pid": pid, "fid": folder_id})
            db.commit()
            page_id = pid
        else:
            page_id = page_res[0]

        pos_res = db.execute(text("SELECT COALESCE(MAX(position), 0) + 1 FROM flipboard.items WHERE page_id = :pid"), {"pid": page_id}).fetchone()
        next_pos = pos_res[0] if pos_res else 1

        item_id = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO flipboard.items (id, page_id, text, assigned_to_name, position, priority, status, source, created_at)
            VALUES (:iid, :page_id, :text, :assigned_to, :pos, 'normal', 'active', 'whatsapp', NOW())
        """), {"iid": item_id, "page_id": page_id, "text": title, "assigned_to": assigned_to, "pos": next_pos})
        db.commit()
        logger.info(f"✨ Auto-synced task to FlipBoard Page 1 of Daily Work ({today_str}): {title} ({assigned_to})")

        # Broadcast real-time WebSocket update to FlipBoard UI (non-blocking)
        try:
            from api.routes.websocket import manager
            msg = {
                "event": "item_created",
                "item": {
                    "id": item_id,
                    "page_id": str(page_id),
                    "text": title,
                    "assigned_to_name": assigned_to,
                    "priority": "normal",
                    "status": "active",
                    "source": "whatsapp"
                }
            }
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(manager.broadcast(msg))
            except Exception:
                pass
        except Exception as wse:
            logger.warning(f"WebSocket broadcast error: {wse}")
    except Exception as e:
        logger.warning(f"Failed to sync task to FlipBoard: {e}")


def _clean_task_title(text: str, assigned_to: str) -> str:
    import re
    cleaned = text
    for prefix in ["hello", "tell yadav to", "tell yadhav to", "tell owner to", "tell ravi to", "tell priya to", "assign yadav to", "assign ravi to", "assign priya to", "add task", "pls", "please"]:
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
    
    if assigned_to:
        for name_part in assigned_to.split():
            if len(name_part) >= 3:
                pattern = re.compile(re.escape(name_part), re.IGNORECASE)
                cleaned = pattern.sub("", cleaned).strip()
    
    cleaned = cleaned.strip(" :-.,!_")
    return cleaned if len(cleaned) >= 3 else text


async def _execute_parsed_intent(parsed: dict, phone: str, sender_name: str, raw_text: str, db: Session) -> str:
    """Executes the parsed intent and returns a human response string."""
    import re
    from sqlalchemy import text
    from datetime import datetime, timedelta

    intent = parsed.get("intent")
    lower = raw_text.lower().strip()

    # --- Jar QR / Quality Check (check first, overrides other intents) ---
    jar_match = re.search(r"jar\s*#?\s*(\d+)", lower)
    if jar_match or intent == "jar_check":
        jar_num = int(jar_match.group(1)) if jar_match else parsed.get("jar_number", 1)
        qual = parsed.get("quality_details", {})
        scores = parsed.get("quality_scores", {})
        smell = qual.get("smell") or ("off" if ("mold" in lower or "bad" in lower or "foul" in lower) else "normal")
        taste = qual.get("taste") or ("off" if ("sour" in lower or "bad" in lower or "off" in lower) else "normal")
        color = qual.get("color", "normal")
        texture = qual.get("texture", "normal")

        # Extract numeric scores if given: "jar 42 taste 8 smell 9 color 7"
        score_matches = re.findall(r"(taste|smell|color|umami|sweetness|aroma)\s*(\d+)", lower)
        for attr, val in score_matches:
            scores[attr] = int(val)

        is_bad = "mold" in lower or "foul" in lower or "bad" in lower or "off" in lower
        jar_url = f"https://gubandhu.initiativesewafoundation.com/admin/jar.html?jar={jar_num}"

        # Try to save quality check to production.jar_events
        try:
            import uuid
            db.execute(text("""
                INSERT INTO production.jar_events (id, jar_id, action, notes, details, performed_by, created_at)
                SELECT :eid, j.id, 'quality_check', :notes, :details::jsonb, :performer, NOW()
                FROM production.jars j WHERE j.jar_number = :jnum
            """), {
                "eid": str(uuid.uuid4()), "jnum": jar_num,
                "notes": raw_text,
                "details": json.dumps({"smell": smell, "taste": taste, "color": color, "scores": scores, "is_alert": is_bad}),
                "performer": sender_name
            })
            db.commit()
        except Exception as je:
            logger.warning(f"Could not save jar event to production DB: {je}")

        if is_bad:
            emit_event("production.jar.quality_alert", {
                "jar_number": jar_num, "smell": smell, "taste": taste, "color": color,
                "recorded_by": sender_name, "observation": raw_text, "scores": scores
            }, actor_name=sender_name, db=db)
            return (f"🚨 *JAR QUALITY ALERT* 🔴\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📦 *Jar #:* {jar_num}\n"
                    f"👃 *Smell:* {smell} | 👅 *Taste:* {taste}\n"
                    f"🎨 *Color:* {color}\n"
                    f"👤 *Checked By:* {sender_name}\n"
                    f"📝 *Notes:* {raw_text[:100]}\n\n"
                    f"⚠️ Owner has been notified!\n"
                    f"_Full timeline: {jar_url}_")
        else:
            score_line = ""
            if scores:
                score_parts = [f"{k.title()}: {v}/10" for k, v in scores.items() if v]
                if score_parts:
                    score_line = f"\n📊 *Scores:* {' | '.join(score_parts)}"

            return (f"🫙 *Jar #{jar_num} Quality Check* ✅\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"👃 Smell: {smell} | 👅 Taste: {taste}\n"
                    f"🎨 Color: {color}{score_line}\n"
                    f"👤 Checked By: {sender_name}\n"
                    f"⏰ Time: Just now\n\n"
                    f"_Full timeline: {jar_url}_")

    # --- Resolve if this is a greeting / status / pending / task-related ---
    is_greeting = lower in ("hi", "hello", "hey", "namaste", "good morning", "gm",
                            "good evening", "shubh prabhat", "namaskar", "hii", "hiii")
    is_status = intent == "status_query" or any(kw in lower for kw in ["status", "pending", "aaj ke kaam", "tasks"])

    # --- MEETING QUERY → Search transcripts with Gemini ---
    if intent == "meeting_query" or any(kw in lower for kw in ["last meeting", "kya decide", "meeting mein", "what did we decide", "meeting summary", "pichli meeting"]):
        return await _handle_meeting_query(raw_text, sender_name)

    # --- MEETING ACTION ITEMS ---
    if intent == "meeting_action_items" or any(kw in lower for kw in ["my action items", "meeting tasks", "action items"]):
        return await _handle_meeting_action_items(sender_name)

    # --- GREETING or STATUS/PENDING → Show real factory summary ---
    if is_greeting or is_status:
        target_emp = parsed.get("assigned_to") if intent == "status_query" else None
        return await _build_real_status_reply(phone, sender_name, db, is_greeting, target_emp)

    # --- Determine if this is a task assignment vs other intent ---
    if intent in ("general_chat", "unknown") and not any(kw in lower for kw in ["jar", "done", "ho gaya"]):
        intent = "task_create"

    is_task_assignment = intent in ("task_create", "task_assignment", "create_task")

    # --- 1. Task Completion ---
    if intent == "task_complete" or ("done" in lower and not is_task_assignment):
        return await _handle_task_complete(parsed, phone, sender_name, raw_text, db)

    # --- 2. Task Creation & Assignment ---
    elif is_task_assignment:
        ai_assigned = parsed.get("assigned_to")
        assigned_to = _resolve_dynamic_assigned_name(raw_text, ai_assigned, db)
        raw_hint = parsed.get("task_hint") or raw_text
        hint = _clean_task_title(raw_hint, assigned_to)

        emit_event("tasks.task.created", {
            "title": hint, "created_by": sender_name, "assigned_to_name": assigned_to
        }, actor_name=sender_name, db=db)

        _sync_task_to_flipboard(hint, assigned_to, db)
        return None  # Template card sent via event_bus, no duplicate text

    # --- 3. Maintenance Update — Actually updates monitoring.rooms in DB ---
    elif intent == "maintenance_update":
        room = parsed.get("room_or_sensor", "Fermentary")
        action = parsed.get("action", "start")
        is_starting = action in ("start", "started", "begin")

        # Find the actual room in monitoring.rooms by fuzzy name match
        room_updated = False
        try:
            room_lower = room.lower().replace("room", "").strip()
            room_result = db.execute(text("""
                SELECT id, name FROM monitoring.rooms
                WHERE LOWER(name) LIKE :rname
                LIMIT 1
            """), {"rname": f"%{room_lower}%"}).fetchone()

            if room_result:
                db.execute(text("""
                    UPDATE monitoring.rooms
                    SET is_under_maintenance = :maint
                    WHERE id = :rid
                """), {"maint": is_starting, "rid": room_result[0]})
                db.commit()
                room = room_result[1]  # Use actual room name from DB
                room_updated = True
                logger.info(f"{'🔧' if is_starting else '✅'} Maintenance {'started' if is_starting else 'ended'} for room '{room}' by {sender_name}")
        except Exception as me:
            logger.warning(f"Failed to update room maintenance status: {me}")

        emit_event(f"monitoring.maintenance.{'started' if is_starting else 'ended'}", {
            "room_name": room, "action": action, "reported_by": sender_name,
            "db_updated": room_updated
        }, actor_name=sender_name, db=db)

        if is_starting:
            return (f"🔧 *Maintenance Started*\n"
                    f"📍 *Room:* {room}\n"
                    f"👤 *By:* {sender_name}\n"
                    f"{'✅ Alerts paused for this room.' if room_updated else '⚠️ Room not found in sensor DB.'}\n\n"
                    f"_Reply 'done cleaning' when finished._")
        else:
            return (f"✅ *Maintenance Completed*\n"
                    f"📍 *Room:* {room}\n"
                    f"👤 *By:* {sender_name}\n"
                    f"{'✅ Alerts resumed for this room.' if room_updated else ''}")

    # --- 4. Issue Reporting ---
    elif intent == "issue_report":
        issue_desc = parsed.get("issue_description", raw_text)
        emit_event("tasks.issue.reported", {
            "title": issue_desc, "reporter_name": sender_name,
            "severity": "high" if "urgent" in lower else "medium"
        }, actor_name=sender_name, db=db)
        return f"⚠️ Issue logged and sent to Owner: *{issue_desc}*"

    # --- 5. Call Confirmation YES ---
    elif intent == "call_confirm_yes":
        emit_event("flipboard.call.confirmed", {
            "confirmed_by": sender_name, "raw_text": raw_text
        }, actor_name=sender_name, db=db)
        return "✅ Call tasks confirmed and added to FlipBoard!"

    # --- 6. Call Confirmation SKIP ---
    elif intent == "call_confirm_skip":
        return "👍 Call tasks skipped."

    # --- 7. Recipe Query ---
    elif intent == "recipe_query" or any(kw in lower for kw in ["recipe", "ingredients", "banane ka tarika", "kitna daalna", "weight", "quantity"]):
        return await _handle_recipe_query(raw_text, sender_name, db)

    # --- 8. Supply / Inventory Request ---
    elif intent == "supply_request" or any(kw in lower for kw in ["khatam", "order karo", "supply", "chahiye", "need more", "finish ho gaya", "out of stock"]):
        item_desc = parsed.get("issue_description") or parsed.get("task_hint") or raw_text
        emit_event("tasks.supply.requested", {
            "title": f"Supply needed: {item_desc}",
            "requested_by": sender_name, "item": item_desc,
            "severity": "high" if "urgent" in lower else "normal"
        }, actor_name=sender_name, db=db)
        _sync_task_to_flipboard(f"🛒 Supply: {item_desc}", "Owner", db)
        return (f"📦 *Supply Request Logged*\n"
                f"🛒 *Item:* {item_desc}\n"
                f"👤 *Requested By:* {sender_name}\n"
                f"✅ Owner has been notified.")

    # --- 9. Production Logging ---
    elif intent == "production_log" or any(kw in lower for kw in ["batch", "packed", "started batch", "production"]):
        product = parsed.get("product_name") or parsed.get("task_hint") or raw_text
        quantity = parsed.get("quantity", "")
        log_text = f"🏭 {product}"
        if quantity:
            log_text += f" ({quantity})"

        emit_event("production.batch.logged", {
            "product": product, "quantity": quantity,
            "logged_by": sender_name, "raw_message": raw_text
        }, actor_name=sender_name, db=db)
        _sync_task_to_flipboard(log_text, sender_name, db)

        return (f"🏭 *Production Logged*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 *Product:* {product}\n"
                + (f"⚖️ *Quantity:* {quantity}\n" if quantity else "")
                + f"👤 *By:* {sender_name}\n"
                f"✅ Added to FlipBoard & notified Owner.")

    # --- 10. Product Catalog Query ---
    elif intent == "product_query" or any(kw in lower for kw in ["products", "catalog", "product list"]):
        return await _handle_product_query(db)

    # --- 11. Report Query ---
    elif intent == "report_query" or any(kw in lower for kw in ["report", "summary report"]):
        return await _handle_report_query(db)

    # --- 12. Sensor Data Query ---
    elif intent == "sensor_query" or any(kw in lower for kw in ["temp history", "temperature report", "sensor log"]):
        return await _handle_sensor_query(raw_text, db)

    # --- 13. Fallback: Smart response ---
    suggested = parsed.get("reply_in_language")
    if suggested:
        return f"🤖 Bandhu: {suggested}"
    return (f"👍 Noted: _{raw_text[:80]}_\n\n"
            f"_Type 'hi' for menu, 'status' for tasks, 'done 1' to complete, or send a 📷 photo._")


async def _build_real_status_reply(phone: str, sender_name: str, db: Session, is_greeting: bool = False, target_employee: str = None) -> str:
    """Build a role-based status reply. Owner sees factory overview, Employee sees their tasks."""
    from sqlalchemy import text
    from datetime import datetime, timedelta

    now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
    today_str = now_ist.strftime("%b %d, %Y")
    hour = now_ist.hour
    if hour < 12:
        greeting = "Good Morning"
        emoji = "☀️"
    elif hour < 17:
        greeting = "Good Afternoon"
        emoji = "🌤️"
    else:
        greeting = "Good Evening"
        emoji = "🌙"

    # Auto-detect role from phone number in DB
    sender_info = _get_sender_info(phone, db)
    role = sender_info["role"]
    display_name = sender_info["display_name"] or sender_name
    is_owner = role in ("owner", "admin")

    # For employees, auto-filter to their own tasks unless they asked for someone else
    if not is_owner and not target_employee:
        target_employee = display_name

    lines = []
    if is_greeting:
        lines.append(f"{emoji} *{greeting}, {display_name}!*")
        lines.append(f"📅 {today_str}")
    else:
        if target_employee and not is_owner:
            lines.append(f"📋 *Your Tasks — {today_str}*")
        elif target_employee:
            lines.append(f"📋 *Tasks for {target_employee}*")
        else:
            lines.append(f"📋 *Factory Status — {today_str}*")
    lines.append("━━━━━━━━━━━━━━━━━━━━")

    # --- 1. Today's FlipBoard Tasks ---
    try:
        # Show items on Current Active FlipBoard (Latest Daily Board + Custom Active Folders)
        # Exactly matches the live FlipBoard UI
        sql = """
            SELECT i.text, i.assigned_to_name, i.status, i.priority, i.position
            FROM flipboard.items i
            JOIN flipboard.pages p ON i.page_id = p.id
            JOIN flipboard.folders f ON p.folder_id = f.id
            WHERE i.status != 'deleted'
              AND (
                  f.id = (
                      SELECT id FROM flipboard.folders
                      WHERE folder_type = 'daily_auto' AND (is_archived IS FALSE OR is_archived IS NULL)
                      ORDER BY created_at DESC LIMIT 1
                  )
                  OR (f.folder_type = 'custom' AND (f.is_archived IS FALSE OR f.is_archived IS NULL))
              )
        """
        params = {}
        if target_employee:
            sql += " AND i.assigned_to_name ILIKE :emp"
            params["emp"] = f"%{target_employee}%"

        sql += """
            ORDER BY
              CASE i.status WHEN 'active' THEN 0 WHEN 'done' THEN 1 ELSE 2 END,
              CASE i.priority WHEN 'urgent' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
              i.position ASC
        """
        
        result = db.execute(text(sql), params)
        tasks = result.fetchall()

        active_tasks = [t for t in tasks if t[2] == 'active']
        done_tasks = [t for t in tasks if t[2] == 'done']

        if active_tasks:
            lines.append(f"\n📋 *Pending Tasks ({len(active_tasks)}):*")
            for idx, t in enumerate(active_tasks[:10], 1):
                prio_icon = "🔴" if t[3] == "urgent" else "🟡"
                assignee = t[1] or "Unassigned"
                task_text = t[0][:45] if len(t[0]) > 45 else t[0]
                lines.append(f"{idx}. {prio_icon} {task_text} — _{assignee}_")
            if len(active_tasks) > 10:
                lines.append(f"   _... +{len(active_tasks) - 10} more_")
        else:
            lines.append("\n✅ *All tasks completed for today!*")

        if done_tasks:
            lines.append(f"\n✅ *Done ({len(done_tasks)}):*")
            for t in done_tasks[:5]:
                lines.append(f"   ✓ {t[0][:40]}")

        lines.append(f"\n📊 Total: {len(active_tasks)} pending, {len(done_tasks)} done")
    except Exception as e:
        logger.warning(f"Error querying FlipBoard tasks: {e}")
        lines.append("\n📋 _Could not load tasks_")

    # --- 2. Sensor Status ---
    try:
        result = db.execute(text("""
            SELECT s.name, r.name as room_name, s.type, s.min_threshold, s.max_threshold, sr.value
            FROM monitoring.sensors s
            LEFT JOIN monitoring.rooms r ON s.room_id = r.id
            LEFT JOIN LATERAL (
                SELECT value FROM monitoring.sensor_readings
                WHERE sensor_id = s.id ORDER BY recorded_at DESC LIMIT 1
            ) sr ON true
            WHERE s.active = true AND s.type = 'temperature' AND sr.value IS NOT NULL
            ORDER BY r.name
        """))
        sensors = result.fetchall()

        if sensors:
            lines.append("\n🌡️ *Sensors:*")
            alerts_found = 0
            for s in sensors:
                val = float(s[5]) if s[5] is not None else None
                name = s[0] or s[1] or "Sensor"
                if val is not None:
                    status = "✅"
                    if s[4] is not None and val > float(s[4]):
                        status = "🔴 HIGH"
                        alerts_found += 1
                    elif s[3] is not None and val < float(s[3]):
                        status = "🔵 LOW"
                        alerts_found += 1
                    thresh = f"({s[3]}-{s[4]})" if s[3] is not None else ""
                    lines.append(f"   {status} {name}: {val:.1f}°C {thresh}")

            if alerts_found == 0:
                # Summarize instead of listing all
                lines_sensor = lines[-len(sensors):]
                # Replace individual lines with summary for cleaner output
                del lines[-len(sensors):]
                lines.append(f"   ✅ All {len(sensors)} sensors normal")
    except Exception as e:
        logger.warning(f"Error querying sensors: {e}")
        lines.append("\n🌡️ _Sensor data unavailable_")

    # --- 3. Active Alerts ---
    try:
        result = db.execute(text("""
            SELECT a.message, a.value, s.name
            FROM monitoring.alerts a
            JOIN monitoring.sensors s ON a.sensor_id = s.id
            WHERE a.resolved = false
            ORDER BY a.created_at DESC LIMIT 3
        """))
        alerts = result.fetchall()
        if alerts:
            lines.append(f"\n🚨 *Active Alerts ({len(alerts)}):*")
            for a in alerts:
                lines.append(f"   ⚠️ {a[2]}: {a[0][:50]}")
    except Exception as e:
        logger.warning(f"Error querying alerts: {e}")

    # --- 4. Role-based quick actions menu ---
    lines.append("\n━━━━━━━━━━━━━━━━━━━━")
    if is_owner:
        lines.append("📱 *Quick Actions:*")
        lines.append("1️⃣ *done 1* — mark task done")
        lines.append("2️⃣ *add [task] - [name]* — assign task")
        lines.append("3️⃣ *check jar [num]* — jar quality")
        lines.append("4️⃣ *recipe [name]* — recipe details")
        lines.append("5️⃣ *meeting summary* — last meeting")
        lines.append("6️⃣ Send a 📷 photo for AI analysis")
    else:
        lines.append("📱 *Quick Actions:*")
        lines.append("1️⃣ *done 1* — mark your task done")
        lines.append("2️⃣ *issue [problem]* — report issue")
        lines.append("3️⃣ *check jar [num]* — jar quality")
        lines.append("4️⃣ Send 📷 photo or 🎤 voice note")

    return "\n".join(lines)


async def _handle_task_complete(parsed: dict, phone: str, sender_name: str, raw_text: str, db: Session) -> str:
    """Handle task completion — find the actual task and mark it done."""
    import re
    from sqlalchemy import text

    lower = raw_text.lower().strip()
    hint = parsed.get("task_hint", raw_text)

    # Check if user specified a task number: "done 1", "done 3", etc.
    num_match = re.search(r"done\s+(\d+)", lower)

    if num_match:
        task_num = int(num_match.group(1))
        # Find the Nth active task in current FlipBoard board
        try:
            result = db.execute(text("""
                SELECT i.id, i.text, i.assigned_to_name
                FROM flipboard.items i
                JOIN flipboard.pages p ON i.page_id = p.id
                JOIN flipboard.folders f ON p.folder_id = f.id
                WHERE i.status = 'active'
                  AND (
                      f.id = (
                          SELECT id FROM flipboard.folders
                          WHERE folder_type = 'daily_auto' AND (is_archived IS FALSE OR is_archived IS NULL)
                          ORDER BY created_at DESC LIMIT 1
                      )
                      OR (f.folder_type = 'custom' AND (f.is_archived IS FALSE OR f.is_archived IS NULL))
                  )
                ORDER BY
                  CASE i.priority WHEN 'urgent' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
                  i.position ASC
            """))
            active_tasks = result.fetchall()

            if task_num < 1 or task_num > len(active_tasks):
                return (f"⚠️ Task #{task_num} not found. "
                        f"There are {len(active_tasks)} active tasks.\n"
                        f"_Reply 'pending' to see the list._")

            task = active_tasks[task_num - 1]
            task_id = task[0]
            task_text = task[1]
            task_assignee = task[2]

            # Mark as done
            db.execute(text("""
                UPDATE flipboard.items
                SET status = 'done', completed_at = NOW()
                WHERE id = :tid
            """), {"tid": str(task_id)})
            db.commit()

            emit_event("tasks.task.completed", {
                "title": task_text, "completed_by": sender_name,
                "assigned_to_name": task_assignee, "raw_message": raw_text
            }, actor_name=sender_name, db=db)

            # Count remaining
            remain = len(active_tasks) - 1
            return (f"✅ *Task Done!*\n"
                    f"📝 {task_text}\n"
                    f"👤 Completed by: {sender_name}\n"
                    f"⏳ {remain} tasks remaining today")

        except Exception as e:
            logger.warning(f"Error marking task done: {e}")
            db.rollback()

    # Fuzzy match: "done miso cleaning", "vinegar room saaf ho gaya"
    try:
        result = db.execute(text("""
            SELECT i.id, i.text, i.assigned_to_name
            FROM flipboard.items i
            JOIN flipboard.pages p ON i.page_id = p.id
            JOIN flipboard.folders f ON p.folder_id = f.id
            WHERE i.status = 'active'
              AND (
                  f.id = (
                      SELECT id FROM flipboard.folders
                      WHERE folder_type = 'daily_auto' AND (is_archived IS FALSE OR is_archived IS NULL)
                      ORDER BY created_at DESC LIMIT 1
                  )
                  OR (f.folder_type = 'custom' AND (f.is_archived IS FALSE OR f.is_archived IS NULL))
              )
            ORDER BY i.position ASC
        """))
        active_tasks = result.fetchall()

        # Try to find a matching task by keyword
        clean_hint = re.sub(r"^(done|ho gaya|khatam|complete|finished)\s*", "", lower).strip()
        best_match = None
        for task in active_tasks:
            task_lower = task[1].lower()
            if clean_hint and (clean_hint in task_lower or any(w in task_lower for w in clean_hint.split() if len(w) > 3)):
                best_match = task
                break

        if best_match:
            db.execute(text("""
                UPDATE flipboard.items SET status = 'done', completed_at = NOW()
                WHERE id = :tid
            """), {"tid": str(best_match[0])})
            db.commit()

            emit_event("tasks.task.completed", {
                "title": best_match[1], "completed_by": sender_name,
                "assigned_to_name": best_match[2], "raw_message": raw_text
            }, actor_name=sender_name, db=db)

            remain = len(active_tasks) - 1
            return (f"✅ *Task Done!*\n"
                    f"📝 {best_match[1]}\n"
                    f"👤 Completed by: {sender_name}\n"
                    f"⏳ {remain} tasks remaining today")
    except Exception as e:
        logger.warning(f"Error in fuzzy task match: {e}")
        db.rollback()

    # Fallback: just log the completion
    emit_event("tasks.task.completed", {
        "title": hint, "completed_by": sender_name, "raw_message": raw_text
    }, actor_name=sender_name, db=db)
    return f"✅ Noted: *{hint}* marked as done by {sender_name}."


async def _download_and_transcribe_audio(media_id: str) -> str:
    """Fetch voice note audio from Meta API and transcribe with Gemini."""
    if not WHATSAPP_ACCESS_TOKEN or not GEMINI_API_KEY:
        return "[Voice Note Received]"

    try:
        # Step 1: Get media URL from Meta
        media_info_url = f"https://graph.facebook.com/v19.0/{media_id}"
        headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(media_info_url, headers=headers)
            if res.status_code != 200:
                return "[Voice Note Audio Unavailable]"
            download_url = res.json().get("url")

            # Step 2: Download raw audio
            res_audio = await client.get(download_url, headers=headers)
            if res_audio.status_code != 200:
                return "[Voice Note Download Error]"
            audio_bytes = res_audio.content

        # Step 3: Call Gemini Audio API
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        prompt = "Transcribe this audio voice note accurately. It may be in Hindi, Hinglish, Marathi, Bengali, or English."
        gem_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "parts": [
                    {"inline_data": {"mime_type": "audio/ogg", "data": audio_b64}},
                    {"text": prompt}
                ]
            }]
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            gem_res = await client.post(gem_url, json=payload)
            if gem_res.status_code == 200:
                text = gem_res.json()["candidates"][0]["content"]["parts"][0]["text"]
                logger.info(f"🎙️ Voice Note Transcribed: {text}")
                return text.strip()
    except Exception as e:
        logger.error(f"Error downloading/transcribing voice note: {e}")

    return "[Voice Note Transcribed]"


async def _analyze_image(image_id: str, caption: str) -> str:
    """Fetch photo from Meta API and analyze with factory-aware Gemini Vision.
    Understands miso, vinegar, fermentation, jars, equipment, and products."""
    if not WHATSAPP_ACCESS_TOKEN or not GEMINI_API_KEY:
        return caption or "[Photo Received]"

    try:
        media_info_url = f"https://graph.facebook.com/v19.0/{image_id}"
        headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(media_info_url, headers=headers)
            if res.status_code != 200:
                return caption or "[Photo Unavailable]"
            download_url = res.json().get("url")

            res_img = await client.get(download_url, headers=headers)
            if res_img.status_code != 200:
                return caption or "[Photo Download Error]"
            img_b64 = base64.b64encode(res_img.content).decode("utf-8")

        prompt = f"""You are the AI assistant for Ground Up Factory in Pune, India — a food/fermentation factory that makes miso, vinegar, koji, and fermented products.

Analyze this image sent by a factory worker on WhatsApp. Caption: '{caption}'

Identify what you see. It could be:
- A PRODUCT photo (miso paste, vinegar, koji, fermented item) → describe the product, estimate quality, note color/texture/consistency
- A JAR photo (fermentation jars with QR codes) → note jar condition, contents, fermentation stage
- A RECIPE/WEIGHT photo (weighing scale, ingredients being measured) → read weights/numbers if visible, identify ingredients
- A MAINTENANCE/ISSUE photo (equipment, leak, damage, dirty area) → describe the issue, severity, recommend action
- A PACKAGING photo (packed products, labels) → note packing quality, label correctness
- A GENERAL WORK photo (cleaning, organizing) → describe the work being done

Be specific and useful. If you can read any text, numbers, or measurements in the image, include them.
Keep response under 150 words. Be direct and actionable."""

        gem_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "parts": [
                    {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}},
                    {"text": prompt}
                ]
            }]
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            gem_res = await client.post(gem_url, json=payload)
            if gem_res.status_code == 200:
                analysis = gem_res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                logger.info(f"📸 Photo analyzed: {analysis[:100]}")
                return f"[PHOTO_ANALYSIS]{analysis}[/PHOTO_ANALYSIS] {caption}".strip()
    except Exception as e:
        logger.error(f"Error analyzing image: {e}")

    return caption or "[Photo Analyzed]"


# ─── Recipe & Production Handlers ────────────────────────────────────────────

async def _handle_recipe_query(question: str, sender_name: str, db: Session) -> str:
    """Answer recipe questions using Gemini with Ground Up factory context."""
    from sqlalchemy import text

    # Try to get recipes from production.recipes table first
    recipe_context = ""
    try:
        result = db.execute(text("""
            SELECT name, ingredients, process_steps, notes
            FROM production.recipes
            WHERE is_active = true
            ORDER BY name
        """))
        recipes = result.fetchall()
        if recipes:
            recipe_lines = []
            for r in recipes:
                recipe_lines.append(f"- {r[0]}: Ingredients: {r[1]}, Steps: {r[2]}")
            recipe_context = "Factory recipes on file:\n" + "\n".join(recipe_lines)
    except Exception:
        # Table might not exist yet — use hardcoded context
        recipe_context = """Factory recipes (Ground Up, Pune):
- White Miso: Soybeans (1kg), Rice Koji (1.2kg), Salt (400g), Water (as needed). Ferment 3-6 months at room temp.
- Red/Brown Miso: Soybeans (1kg), Barley Koji (800g), Salt (500g). Ferment 12-18 months.
- Rice Vinegar: Rice (1kg), Koji starter, Water (3L). Ferment 2-3 weeks for alcohol, then 6-8 weeks for acetic acid.
- Koji: Steamed rice inoculated with Aspergillus oryzae spores. 48hr incubation at 30°C, 80% humidity.
- Soy Sauce (experimental): Soybeans + wheat + salt brine. 6+ months fermentation.
- Shio Koji: Rice Koji (200g), Salt (60g), Water (300ml). Ferment 7-10 days at room temp."""

    prompt = f"""You are the recipe expert for Ground Up Factory (Pune, India) — a food/fermentation factory making miso, vinegar, koji, and fermented products.

{recipe_context}

Question from {sender_name}: "{question}"

Answer the recipe question accurately. Include specific weights, ratios, temperatures, and timing.
If they ask about a product you don't have a recipe for, suggest the closest match.
Reply in Hinglish (mix of Hindi and English) if the question was in Hindi/Hinglish, otherwise English.
Keep response concise — under 200 words."""

    try:
        gem_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3}
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(gem_url, json=payload)
            if res.status_code == 200:
                answer = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                return (f"🍳 *Recipe Info*\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"{answer}")
    except Exception as e:
        logger.error(f"[RecipeQuery] Error: {e}")

    return "Sorry, couldn't look up the recipe right now. Try asking again."


# ─── Meeting Intelligence Handlers ───────────────────────────────────────────

async def _handle_meeting_query(question: str, sender_name: str) -> str:
    """Answer a question about the most recent meeting using Gemini."""
    try:
        from groundup_webhooks.meetings_db import get_latest_meeting
        from groundup_webhooks.meetings_gemini import chat_with_meeting

        meeting = get_latest_meeting()
        if not meeting:
            return "🎙️ No meetings have been recorded yet. Use the MeetingRecorder device to record your first meeting!"

        transcript = meeting.get("transcript", "")
        if not transcript:
            return "🎙️ I have a meeting record but couldn't find the transcript. Please check the Meeting Hub."

        answer = await chat_with_meeting(transcript, question, [])

        hub_url = f"https://gubandhu.initiativesewafoundation.com/meetings/#{meeting['id']}"
        return (
            f"🎙️ *From last meeting ({meeting.get('title', 'Recent Meeting')})*\n\n"
            f"{answer}\n\n"
            f"_Full meeting: {hub_url}_"
        )
    except Exception as e:
        logger.error(f"[MeetingQuery] Error: {e}")
        return "Sorry, I couldn't search the meeting right now. Try again in a moment."


async def _handle_meeting_action_items(sender_name: str) -> str:
    """List pending action items from the latest meeting."""
    try:
        from groundup_webhooks.meetings_db import get_latest_meeting

        meeting = get_latest_meeting()
        if not meeting:
            return "🎙️ No meetings recorded yet."

        action_items = meeting.get("action_items", [])
        if not action_items:
            return "✅ No action items in the last meeting."

        pending = [ai for ai in action_items if isinstance(ai, dict) and not ai.get("approved", False)]
        done = [ai for ai in action_items if isinstance(ai, dict) and ai.get("approved", False)]

        lines = [f"📌 *Action Items — {meeting.get('title', 'Last Meeting')}*", ""]

        if pending:
            lines.append("⏳ *Pending:*")
            for ai in pending[:8]:
                task = ai.get("task", "")
                assignee = ai.get("assignee", "")
                due = ai.get("due", "")
                item = f"• {assignee} → {task}" if assignee else f"• {task}"
                if due:
                    item += f" _(by {due})_"
                lines.append(item)

        if done:
            lines.append("")
            lines.append(f"✅ {len(done)} item(s) approved")

        hub_url = f"https://gubandhu.initiativesewafoundation.com/meetings/#{meeting['id']}"
        lines.append(f"\n_Approve items: {hub_url}_")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[MeetingActionItems] Error: {e}")
        return "Sorry, couldn't fetch action items right now."


# ─── Products, Reports & Sensor Intelligence Handlers ──────────────────────

async def _handle_product_query(db: Session) -> str:
    """Fetch product catalog summary from production DB or default list."""
    from sqlalchemy import text
    try:
        result = db.execute(text("""
            SELECT name, category, batch_size, status
            FROM production.products
            WHERE is_active = true
            ORDER BY category, name
        """))
        products = result.fetchall()
        if products:
            lines = ["🏭 *Ground Up Product Catalog*", "━━━━━━━━━━━━━━━━━━━━"]
            by_cat = {}
            for p in products:
                cat = p[1] or "General"
                by_cat.setdefault(cat, []).append(f"• *{p[0]}* (Batch: {p[2] or 'Standard'})")
            for cat, items in by_cat.items():
                lines.append(f"\n📦 *{cat.title()}:*")
                lines.extend(items)
            return "\n".join(lines)
    except Exception as e:
        logger.warning(f"Could not query production.products: {e}")

    # Default catalog fallback
    return (
        "🏭 *Ground Up Products*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📦 *Miso:* White Miso, Red Miso, Barley Miso\n"
        "🍾 *Vinegar:* Rice Vinegar, Apple Cider Vinegar\n"
        "🌾 *Koji:* Rice Koji, Shio Koji\n\n"
        "_Reply 'recipe [product]' for ingredients & details._"
    )


async def _handle_report_query(db: Session) -> str:
    """Build a consolidated daily/weekly summary report for WhatsApp."""
    from sqlalchemy import text
    from datetime import datetime, timedelta

    now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
    today_str = now_ist.strftime("%b %d, %Y")

    lines = [f"📊 *Ground Up Factory Summary Report*", f"📅 *{today_str}*", "━━━━━━━━━━━━━━━━━━━━"]

    # 1. FlipBoard tasks summary
    try:
        res = db.execute(text("""
            SELECT status, COUNT(*)
            FROM flipboard.items i
            JOIN flipboard.pages p ON i.page_id = p.id
            JOIN flipboard.folders f ON p.folder_id = f.id
            WHERE f.folder_type = 'daily_auto' AND f.created_at::date = CURRENT_DATE
            GROUP BY status
        """)).fetchall()
        counts = {row[0]: row[1] for row in res}
        active_c = counts.get('active', 0)
        done_c = counts.get('done', 0)
        lines.append(f"📋 *Tasks Today:* {done_c} completed, {active_c} pending")
    except Exception as e:
        logger.warning(f"Report query tasks error: {e}")

    # 2. Sensor health
    try:
        s_res = db.execute(text("""
            SELECT COUNT(*),
                   SUM(CASE WHEN active = true THEN 1 ELSE 0 END)
            FROM monitoring.sensors
        """)).fetchone()
        lines.append(f"🌡️ *Sensors Active:* {s_res[1] or 0} / {s_res[0] or 0}")
    except Exception as e:
        logger.warning(f"Report query sensors error: {e}")

    # 3. Unresolved Alerts
    try:
        a_res = db.execute(text("""
            SELECT COUNT(*) FROM monitoring.alerts WHERE resolved = false
        """)).fetchone()
        lines.append(f"🚨 *Active Alerts:* {a_res[0] or 0}")
    except Exception as e:
        logger.warning(f"Report query alerts error: {e}")

    lines.append("\n━━━━━━━━━━━━━━━━━━━━")
    lines.append("_Full Dashboard: https://gubandhu.initiativesewafoundation.com/_")
    return "\n".join(lines)


async def _handle_sensor_query(raw_text: str, db: Session) -> str:
    """Fetch detailed sensor telemetry and history."""
    from sqlalchemy import text
    try:
        result = db.execute(text("""
            SELECT s.name, r.name as room_name, sr.value, sr.recorded_at
            FROM monitoring.sensors s
            LEFT JOIN monitoring.rooms r ON s.room_id = r.id
            LEFT JOIN LATERAL (
                SELECT value, recorded_at FROM monitoring.sensor_readings
                WHERE sensor_id = s.id ORDER BY recorded_at DESC LIMIT 1
            ) sr ON true
            WHERE s.active = true
            ORDER BY r.name
        """)).fetchall()

        if result:
            lines = ["🌡️ *Sensor Telemetry & History*", "━━━━━━━━━━━━━━━━━━━━"]
            for r in result:
                sname = r[0] or "Sensor"
                rname = r[1] or "Room"
                val = f"{float(r[2]):.1f}°C" if r[2] is not None else "No data"
                lines.append(f"• *{sname}* ({rname}): {val}")
            lines.append("\n_For full historical charts, open the Monitoring Dashboard._")
            return "\n".join(lines)
    except Exception as e:
        logger.warning(f"Sensor query error: {e}")

    return "🌡️ Sensor telemetry is currently being updated. Please try again in 1 minute."

