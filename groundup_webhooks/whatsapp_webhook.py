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
        profile_name = contacts[0].get("profile", {}).get("name", "Unknown") if contacts else "Unknown"
        sender_info = _get_sender_info(sender_phone, db)
        sender_name = sender_info.get("display_name") or profile_name
        msg_type = message.get("type", "text")
        meta_msg_id = message.get("id", "")

        logger.info(f"📩 Incoming WhatsApp from {sender_phone} ({sender_name} | profile={profile_name}) | type={msg_type}")

        raw_content = ""
        media_url = None

        audio_id = None
        # 1. Handle Text Messages
        if msg_type == "text":
            raw_content = message.get("text", {}).get("body", "").strip()

        # 2. Handle Voice Notes (Audio)
        elif msg_type == "audio":
            audio_id = message.get("audio", {}).get("id")
            raw_content = "[Voice Note Audio]"

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
        msg_record_id = msg_record.id

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
                    # High-Speed Single-Pass Voice Processing
                    if msg_type == "audio" and audio_id:
                        transcript, parsed = loop.run_until_complete(
                            _download_transcribe_and_parse_audio(audio_id, sender_phone, sender_name, bg_db)
                        )
                        current_raw = transcript
                        try:
                            m_rec = bg_db.query(WhatsAppMessage).filter(WhatsAppMessage.id == msg_record_id).first()
                            if m_rec:
                                m_rec.raw_content = current_raw
                                bg_db.commit()
                        except Exception:
                            pass

                        reply_text = loop.run_until_complete(_execute_parsed_intent(parsed, sender_phone, sender_name, current_raw, bg_db))
                        if reply_text:
                            # 1. Send native Voice Note Audio reply
                            try:
                                from groundup_webhooks.voice_engine import synthesize_speech, send_whatsapp_audio
                                lang = parsed.get("language", "hi")
                                speech_res = synthesize_speech(reply_text, language=lang)
                                if speech_res:
                                    _, audio_url = speech_res
                                    loop.run_until_complete(send_whatsapp_audio(sender_phone, audio_url, bg_db, sender_name="Bandhu Voice"))
                            except Exception as ve:
                                logger.warning(f"Voice reply synthesis skipped: {ve}")

                            # 2. Send formatted text summary card
                            text_card = f"🎙️ *Voice Note:* \"{current_raw}\"\n━━━━━━━━━━━━━━━━━━━━\n{reply_text}"
                            loop.run_until_complete(send_whatsapp_text(sender_phone, text_card, bg_db, sender_name="GroundUp Bot"))

                    # For photos: extract analysis, distinguish QR code vs product photo, and save only product photos to timeline
                    elif is_photo and "[PHOTO_ANALYSIS]" in raw_content:
                        analysis = raw_content.split("[PHOTO_ANALYSIS]")[1].split("[/PHOTO_ANALYSIS]")[0].strip()
                        photo_url = ""
                        if "[PHOTO_URL]" in raw_content:
                            photo_url = raw_content.split("[PHOTO_URL]")[1].split("[/PHOTO_URL]")[0].strip()
                        
                        is_qr = ("[IS_QR]1[/IS_QR]" in raw_content)
                        extracted_jar = None
                        if "[JAR_NUM]" in raw_content:
                            j_str = raw_content.split("[JAR_NUM]")[1].split("[/JAR_NUM]")[0].strip()
                            if j_str.isdigit():
                                extracted_jar = int(j_str)

                        caption_part = raw_content.split("[/PHOTO_ANALYSIS]")[-1]
                        for tag in ["[PHOTO_URL]", "[/PHOTO_URL]", "[IS_QR]", "[/IS_QR]", "[JAR_NUM]", "[/JAR_NUM]"]:
                            if tag in caption_part:
                                caption_part = caption_part.split(tag)[-1]
                        caption_part = caption_part.strip()

                        if not extracted_jar:
                            # Fallback regex search on caption and analysis
                            j_match = re.search(r"jar\s*#?\s*(\d+)", f"{caption_part} {analysis}".lower())
                            if j_match:
                                extracted_jar = int(j_match.group(1))

                        ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
                        current_time_str = ist_now.strftime("%I:%M %p, %d %b %Y")

                        # 1. User scanned a QR Code sticker -> DO NOT save sticker photo, prompt for product photo
                        if is_qr and extracted_jar:
                            jar_url = f"https://gu-production.initiativesewafoundation.com/jar.html?jar={extracted_jar}"
                            photo_reply = (
                                f"📱 *Jar #{extracted_jar} QR Code Scanned* ✅\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"👤 *Scanned by:* {sender_name}\n"
                                f"⏰ *Time:* {current_time_str}\n\n"
                                f"📸 *Next Step:* Please take a photo of the *PRODUCT / FERMENT* inside Jar #{extracted_jar} to add to its quality timeline!\n\n"
                                f"_Full timeline: {jar_url}_"
                            )
                        # 2. Actual Product / Ferment photo for Jar -> SAVE to timeline!
                        elif extracted_jar:
                            try:
                                import uuid
                                jar_url = f"https://gu-production.initiativesewafoundation.com/jar.html?jar={extracted_jar}"
                                bg_db.execute(text("""
                                    INSERT INTO production.jar_timeline (id, jar_id, action, details, recorded_by, created_at)
                                    SELECT :eid, j.id, 'photo_inspection', CAST(:details AS jsonb), NULL, NOW()
                                    FROM production.jars j WHERE j.jar_number = :jnum
                                """), {
                                    "eid": str(uuid.uuid4()), "jnum": extracted_jar,
                                    "details": json.dumps({
                                        "notes": analysis,
                                        "photo_url": photo_url,
                                        "caption": caption_part,
                                        "recorded_by": sender_name,
                                        "timestamp": current_time_str
                                    })
                                })
                                bg_db.commit()

                                photo_reply = (
                                    f"📸 *Jar #{extracted_jar} Product Photo Saved to Timeline!* ✅\n"
                                    f"━━━━━━━━━━━━━━━━━━━━\n"
                                    f"🔍 *AI Quality Analysis:* {analysis}\n\n"
                                    f"👤 *Uploaded by:* {sender_name}\n"
                                    f"⏰ *Time:* {current_time_str}\n\n"
                                    f"🖼️ *View in Jar Timeline:*\n"
                                    f"👉 {jar_url}"
                                )
                            except Exception as pe:
                                logger.error(f"Error saving photo to jar timeline: {pe}")
                                photo_reply = f"📸 *Photo Analysis*\n━━━━━━━━━━━━━━━━━━━━\n{analysis}\n"
                                if caption_part:
                                    photo_reply += f"\n📝 *Your caption:* {caption_part}"
                        else:
                            # General photo
                            photo_reply = (
                                f"📸 *Photo Analysis*\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"{analysis}\n\n"
                                f"💡 *Tip:* To attach product photos directly to a Jar timeline, send with a caption like *\"jar 1\"* or *\"jar 42\"*."
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
                        # Normal text/button flow
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


def _broadcast_to_flipboard(msg: dict):
    """Safely broadcasts a real-time event to connected FlipBoard WebSocket clients across threads and processes."""
    # 1. Try local manager if running on main event loop
    try:
        from api.routes.websocket import manager
        import asyncio
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        if loop and loop.is_running():
            loop.create_task(manager.broadcast(msg))
            return
    except Exception:
        pass

    # 2. Fire via internal broadcast HTTP endpoint (works cross-thread & cross-process)
    def _fire_http():
        try:
            import httpx
            for base_url in ["http://127.0.0.1:8000", "http://localhost:8000", "http://127.0.0.1:8080", "https://gubandhu.initiativesewafoundation.com"]:
                try:
                    res = httpx.post(f"{base_url}/api/internal/broadcast", json=msg, timeout=0.8)
                    if res.status_code == 200:
                        break
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"Broadcast HTTP failed: {e}")

    import threading
    threading.Thread(target=_fire_http, daemon=True).start()


def _get_today_daily_folder(db: Session):
    """Finds or creates today's active Daily Work folder matching /api/today."""
    import uuid
    from datetime import datetime, timedelta
    from sqlalchemy import text

    today_ist = (datetime.utcnow() + timedelta(hours=5, minutes=30)).date()
    today_str = today_ist.strftime("%Y-%m-%d")

    folder_res = db.execute(text("""
        SELECT id FROM flipboard.folders
        WHERE folder_type = 'daily_auto' 
          AND (is_archived IS FALSE OR is_archived IS NULL)
          AND title LIKE :tpattern
        ORDER BY created_at DESC LIMIT 1
    """), {"tpattern": f"Daily Work - {today_str}%"}).fetchone()

    if folder_res:
        return str(folder_res[0]), today_str

    # Auto-create if not exists yet
    fid = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO flipboard.folders (id, title, folder_type, is_archived, created_at)
        VALUES (:fid, :title, 'daily_auto', false, NOW())
    """), {"fid": fid, "title": f"Daily Work - {today_str}"})
    db.commit()

    # Create Page 1
    pid = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO flipboard.pages (id, folder_id, page_number, page_date, created_at)
        VALUES (:pid, :fid, 1, :pdate, NOW())
    """), {"pid": pid, "fid": fid, "pdate": today_ist})
    db.commit()

    # Rollover pending tasks from past 7 days
    try:
        pos = 1
        for days_back in range(1, 8):
            check_date = (today_ist - timedelta(days=days_back)).strftime("%Y-%m-%d")
            prev_folder = db.execute(text("""
                SELECT id FROM flipboard.folders
                WHERE folder_type = 'daily_auto'
                  AND (is_archived IS FALSE OR is_archived IS NULL)
                  AND title LIKE :pat
                ORDER BY created_at DESC LIMIT 1
            """), {"pat": f"Daily Work - {check_date}%"}).fetchone()

            if prev_folder:
                prev_fid = prev_folder[0]
                prev_items = db.execute(text("""
                    SELECT i.text, i.assigned_to_name, i.priority
                    FROM flipboard.items i
                    JOIN flipboard.pages p ON i.page_id = p.id
                    WHERE p.folder_id = :pfid AND i.status = 'active'
                    ORDER BY i.position ASC
                """), {"pfid": prev_fid}).fetchall()

                for p_text, p_assignee, p_prio in prev_items:
                    exists = db.execute(text("""
                        SELECT id FROM flipboard.items
                        WHERE page_id = :pid AND text = :text AND status != 'deleted'
                    """), {"pid": pid, "text": p_text}).fetchone()
                    if not exists:
                        db.execute(text("""
                            INSERT INTO flipboard.items (id, page_id, text, assigned_to_name, position, priority, status, source, created_at)
                            VALUES (:iid, :pid, :text, :assigned, :pos, :prio, 'active', 'rollover', NOW())
                        """), {
                            "iid": str(uuid.uuid4()), "pid": pid, "text": p_text,
                            "assigned": p_assignee, "pos": pos, "prio": p_prio or "normal"
                        })
                        pos += 1
                break
        db.commit()
    except Exception as e:
        logger.warning(f"Failed to rollover tasks in WhatsApp helper: {e}")
        db.rollback()

    return fid, today_str


def _sync_task_to_flipboard(title: str, assigned_to: str, db: Session):
    """Auto-creates item on FlipBoard on Page 1 of TODAY'S active Daily Work folder."""
    try:
        import uuid
        from sqlalchemy import text
        
        folder_id, today_str = _get_today_daily_folder(db)

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

        # Broadcast real-time WebSocket update to FlipBoard UI
        _broadcast_to_flipboard({
            "event": "item_created",
            "page_id": str(page_id),
            "item": {
                "id": item_id,
                "page_id": str(page_id),
                "text": title,
                "assigned_to_name": assigned_to,
                "priority": "normal",
                "status": "active",
                "source": "whatsapp"
            }
        })
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
        jar_url = f"https://gu-production.initiativesewafoundation.com/jar.html?jar={jar_num}"

        # 1. Query current Jar & Timeline from DB
        jar_row = db.execute(text("SELECT id, jar_number, status, created_at FROM production.jars WHERE jar_number = :jnum"), {"jnum": jar_num}).fetchone()
        timeline_rows = []
        if jar_row:
            timeline_rows = db.execute(text("SELECT action, details, created_at FROM production.jar_timeline WHERE jar_id = :jid ORDER BY created_at DESC"), {"jid": str(jar_row[0])}).fetchall()

        # Check if user is PROVIDING scores / observations or JUST ASKING status
        scores = parsed.get("quality_scores", {}) or {}
        score_matches = re.findall(r"(taste|smell|color|umami|sweetness|aroma)\s*(\d+)", lower)
        for attr, val in score_matches:
            scores[attr] = int(val)

        has_scores = bool(scores)
        has_observation = any(w in lower for w in ["mold", "bad", "foul", "sour", "bitter", "good", "sahi", "theek", "kharaab", "thik", "normal", "badhiya"]) and not (lower.startswith("check jar") and len(lower.split()) <= 3)

        # 1. UNASSIGNED JAR (Clean jar with no batch packaged)
        is_packaging = any(w in lower for w in ["package", "pack ", "assign recipe", "scale batch"])
        if is_packaging and jar_num:
            # Extract material & capacity
            mat = "Glass" if ("glass" in lower or "kanch" in lower or "sheesha" in lower) else "Plastic"
            cap_match = re.search(r"(\d+)\s*(l|litre|liter|kg)?", lower)
            cap = int(cap_match.group(1)) if cap_match else (20 if mat == "Glass" else 50)
            jt_label = f"{mat} ({cap}L)"
            
            rec_name = "White Miso" if "white" in lower else "Red Miso" if "red" in lower else "Rice Koji" if "koji" in lower else "Miso Ferment"
            
            try:
                import uuid
                now_dt = datetime.utcnow()
                ready_dt = now_dt + timedelta(days=90)
                db.execute(text("""
                    INSERT INTO production.jar_timeline (id, jar_id, action, details, recorded_by, created_at)
                    SELECT :eid, j.id, 'packaged', CAST(:details AS jsonb), NULL, NOW()
                    FROM production.jars j WHERE j.jar_number = :jnum
                """), {
                    "eid": str(uuid.uuid4()), "jnum": jar_num,
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
                logger.error(f"Error packaging batch via WhatsApp: {pe}")

        if not timeline_rows:
            return (
                f"🫙 *Jar #{jar_num} is UNASSIGNED (Clean Jar)* ✨\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 *Status:* Clean & Empty (Available for batch)\n"
                f"👤 *Checked by:* {sender_name}\n\n"
                f"📝 *To package a new recipe batch into Jar #{jar_num}:*\n"
                f"1️⃣ Open interactive recipe tool:\n"
                f"👉 {jar_url}\n"
                f"2️⃣ Or reply: *\"package 50kg miso into jar {jar_num} plastic 50L\"*"
            )

        # 2. ACTIVE JAR & USER IS JUST CHECKING STATUS (not submitting scores)
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
                
                jt = details.get("jar_type") or (f"{details.get('material', 'Plastic')} ({details.get('capacity_litres', 50)}L)" if details.get("material") else None)
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
                f"📝 *Please record your inspection:*\n"
                f"• Reply with ratings: *\"jar {jar_num} taste 8 smell 9 color 8\"*\n"
                f"• Or describe: *\"jar {jar_num} smell good, taste normal, no mold\"*\n"
                f"• 📸 *Send a photo of the ferment for AI vision check!*\n\n"
                f"_Full interactive form: {jar_url}_"
            )

        # 3. USER IS SUBMITTING ACTUAL SCORES / OBSERVATIONS
        qual = parsed.get("quality_details", {}) or {}
        smell = qual.get("smell") or ("off" if ("mold" in lower or "bad" in lower or "foul" in lower or "kharaab" in lower) else "good" if ("good" in lower or "sahi" in lower or "theek" in lower) else "normal")
        taste = qual.get("taste") or ("off" if ("sour" in lower or "bad" in lower or "bitter" in lower or "kharaab" in lower) else "normal")
        color = qual.get("color", "normal")
        is_bad = "mold" in lower or "foul" in lower or "bad" in lower or "kharaab" in lower

        # Save quality check to production.jar_timeline
        try:
            import uuid
            db.execute(text("""
                INSERT INTO production.jar_timeline (id, jar_id, action, details, recorded_by, created_at)
                SELECT :eid, j.id, 'quality_check', CAST(:details AS jsonb), NULL, NOW()
                FROM production.jars j WHERE j.jar_number = :jnum
            """), {
                "eid": str(uuid.uuid4()), "jnum": jar_num,
                "details": json.dumps({"smell": smell, "taste": taste, "color": color, "scores": scores, "is_alert": is_bad, "notes": raw_text, "recorded_by": sender_name}),
            })
            db.commit()
        except Exception as je:
            logger.warning(f"Could not save jar event to production DB: {je}")
            try:
                db.rollback()
            except Exception:
                pass

        ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
        current_time_str = ist_now.strftime("%I:%M %p, %d %b %Y")

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
                    f"⏰ *Time:* {current_time_str}\n"
                    f"📝 *Notes:* {raw_text[:100]}\n\n"
                    f"⚠️ Owner has been notified!\n"
                    f"_Full timeline: {jar_url}_")
        else:
            score_line = ""
            if scores:
                score_parts = [f"{k.title()}: {v}/10" for k, v in scores.items() if v]
                if score_parts:
                    score_line = f"\n📊 *Scores:* {' | '.join(score_parts)}"

            return (f"🫙 *Jar #{jar_num} Quality Check Recorded* ✅\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"👃 Smell: {smell} | 👅 Taste: {taste}\n"
                    f"🎨 Color: {color}{score_line}\n"
                    f"👤 Checked By: {sender_name}\n"
                    f"⏰ Time: {current_time_str}\n\n"
                    f"_Full timeline: {jar_url}_")

    # ── 0. TWO-WAY HUMAN ESCALATION CHECK ──
    try:
        from groundup_webhooks.escalation_engine import check_and_handle_owner_reply, escalate_to_human, is_active_escalation
        owner_reply = await check_and_handle_owner_reply(phone, raw_text, db)
        if owner_reply:
            return owner_reply

        # If user explicitly requests human help or says they can't figure it out
        if any(kw in lower for kw in ["gaya se baat", "owner se baat", "talk to human", "connect manager", "help chahiye owner", "escalate", "sir se baat", "call lagao"]):
            return await escalate_to_human(phone, sender_name, raw_text, "User requested human assistance", db)
    except Exception as ee:
        logger.warning(f"Escalation engine notice: {ee}")

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
    task_assignment_keywords = ["tell ", "assign ", "add task", "add ", "clean ", "naya kaam", "karo ", "check ", "dekh lo", "saaf karo", "pack "]
    is_explicit_task = intent in ("task_create", "task_assignment", "create_task")
    is_keyword_task = any(kw in lower for kw in task_assignment_keywords) and len(lower.split()) >= 3

    is_task_assignment = is_explicit_task or (intent in ("general_chat", "unknown") and is_keyword_task)

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
    elif intent == "call_confirm_yes" or lower in ("yes", "y", "ha", "haan", "confirm"):
        try:
            import uuid as _uuid
            rec_row = db.execute(text("""
                SELECT id, filename, employee_name, extracted_tasks, extracted_issues
                FROM flipboard.call_recordings
                WHERE owner_confirmed = false AND (status IS NULL OR status != 'skipped')
                ORDER BY uploaded_at DESC LIMIT 1
            """)).fetchone()

            if not rec_row:
                return "ℹ️ No pending call recordings to confirm."

            rec_id, filename, emp_name, tasks_json, issues_json = rec_row
            import json as _json
            extracted_tasks = _json.loads(tasks_json) if isinstance(tasks_json, str) else (tasks_json or [])
            extracted_issues = _json.loads(issues_json) if isinstance(issues_json, str) else (issues_json or [])

            folder_id, today_str = _get_today_daily_folder(db)
            page_res = db.execute(text("""
                SELECT id FROM flipboard.pages WHERE folder_id = :fid AND page_number = 1 LIMIT 1
            """), {"fid": folder_id}).fetchone()
            page_id = page_res[0] if page_res else None

            created_count = 0
            if page_id:
                pos_res = db.execute(text("SELECT COALESCE(MAX(position), 0) + 1 FROM flipboard.items WHERE page_id = :pid"), {"pid": page_id}).fetchone()
                max_pos = pos_res[0] if pos_res else 1

                for task in extracted_tasks:
                    item_id = str(_uuid.uuid4())
                    task_text = task.get("text", "")
                    task_assignee = task.get("assigned_to", emp_name or "Staff")
                    task_prio = task.get("priority", "normal")
                    db.execute(text("""
                        INSERT INTO flipboard.items (id, page_id, text, assigned_to_name, position, priority, status, source, created_at)
                        VALUES (:iid, :pid, :text, :assigned, :pos, :prio, 'active', 'call', NOW())
                    """), {"iid": item_id, "pid": page_id, "text": task_text, "assigned": task_assignee, "pos": max_pos, "prio": task_prio})
                    max_pos += 1
                    created_count += 1
                    _broadcast_to_flipboard({
                        "event": "item_created",
                        "page_id": str(page_id),
                        "item": {"id": item_id, "page_id": str(page_id), "text": task_text, "assigned_to_name": task_assignee, "priority": task_prio, "status": "active", "source": "call"}
                    })

                for issue in extracted_issues:
                    item_id = str(_uuid.uuid4())
                    issue_text = f"[ISSUE] {issue.get('text', '')}"
                    issue_sev = issue.get("severity", "medium")
                    issue_prio = "urgent" if issue_sev == "high" else "normal"
                    db.execute(text("""
                        INSERT INTO flipboard.items (id, page_id, text, assigned_to_name, position, priority, status, source, created_at)
                        VALUES (:iid, :pid, :text, :assigned, :pos, :prio, 'active', 'call', NOW())
                    """), {"iid": item_id, "pid": page_id, "text": issue_text, "assigned": emp_name or "Owner", "pos": max_pos, "prio": issue_prio})
                    max_pos += 1
                    created_count += 1
                    _broadcast_to_flipboard({
                        "event": "item_created",
                        "page_id": str(page_id),
                        "item": {"id": item_id, "page_id": str(page_id), "text": issue_text, "assigned_to_name": emp_name or "Owner", "priority": issue_prio, "status": "active", "source": "call"}
                    })

            # Mark recording confirmed
            db.execute(text("""
                UPDATE flipboard.call_recordings
                SET owner_confirmed = true, tasks_created_count = :cnt, confirmed_at = NOW(), status = 'confirmed'
                WHERE id = :rid
            """), {"cnt": created_count, "rid": rec_id})
            db.commit()

            emit_event("flipboard.call.confirmed", {
                "recording_id": str(rec_id), "tasks_created": created_count, "confirmed_by": sender_name
            }, actor_name=sender_name, db=db)

            return f"✅ Confirmed! {created_count} task(s) from call added to FlipBoard."
        except Exception as ce:
            logger.error(f"Error confirming call recording via WhatsApp: {ce}")
            try:
                db.rollback()
            except Exception:
                pass
            return "⚠️ Error confirming call recording tasks."

    # --- 6. Call Confirmation SKIP ---
    elif intent == "call_confirm_skip" or lower in ("skip", "nahi", "no", "ignore"):
        try:
            db.execute(text("""
                UPDATE flipboard.call_recordings
                SET status = 'skipped'
                WHERE id = (
                    SELECT id FROM flipboard.call_recordings
                    WHERE owner_confirmed = false AND (status IS NULL OR status != 'skipped')
                    ORDER BY uploaded_at DESC LIMIT 1
                )
            """))
            db.commit()
            return "👍 Call tasks skipped."
        except Exception as se:
            logger.error(f"Error skipping call recording: {se}")
            try:
                db.rollback()
            except Exception:
                pass
            return "👍 Call tasks skipped."

    # ── Specialist Agent Orchestration (Chef Gaya, Production, Monitoring) ──
    try:
        from groundup_agents.orchestrator import orchestrator
        agent_reply = await orchestrator.dispatch(raw_text, phone, sender_name, parsed, db)
        if agent_reply:
            return agent_reply
    except Exception as oe:
        logger.error(f"Error in AgentOrchestrator dispatch: {oe}")

    # Fallback: Smart response
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
        folder_id, folder_date_str = _get_today_daily_folder(db)

        sql = """
            SELECT i.text, i.assigned_to_name, i.status, i.priority, i.position
            FROM flipboard.items i
            JOIN flipboard.pages p ON i.page_id = p.id
            WHERE p.folder_id = :fid
              AND i.status != 'deleted'
        """
        params = {"fid": folder_id}
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
    from datetime import datetime, timedelta
    from sqlalchemy import text

    lower = raw_text.lower().strip()
    hint = parsed.get("task_hint", raw_text)
    folder_id, today_str = _get_today_daily_folder(db)

    # Check if user specified a task number: "done 1", "done 3", etc.
    num_match = re.search(r"done\s+(\d+)", lower)

    if num_match:
        task_num = int(num_match.group(1))
        # Find the Nth active task in TODAY's FlipBoard board
        try:
            result = db.execute(text("""
                SELECT i.id, i.text, i.assigned_to_name, i.page_id
                FROM flipboard.items i
                JOIN flipboard.pages p ON i.page_id = p.id
                WHERE p.folder_id = :fid
                  AND i.status = 'active'
                ORDER BY
                  CASE i.priority WHEN 'urgent' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
                  i.position ASC
            """), {"fid": folder_id})
            active_tasks = result.fetchall()

            if task_num < 1 or task_num > len(active_tasks):
                return (f"⚠️ Task #{task_num} not found. "
                        f"There are {len(active_tasks)} active tasks today.\n"
                        f"_Reply 'pending' to see the list._")

            task = active_tasks[task_num - 1]
            task_id = task[0]
            task_text = task[1]
            task_assignee = task[2]
            page_id = task[3]

            # Mark as done with hide_after
            now_dt = datetime.utcnow()
            hide_dt = now_dt + timedelta(minutes=30)
            db.execute(text("""
                UPDATE flipboard.items
                SET status = 'done', completed_at = :cat, hide_after = :hat
                WHERE id = :tid
            """), {"tid": str(task_id), "cat": now_dt, "hat": hide_dt})
            db.commit()

            # Broadcast real-time update to FlipBoard UI
            _broadcast_to_flipboard({
                "event": "item_done",
                "page_id": str(page_id),
                "item": {
                    "id": str(task_id),
                    "page_id": str(page_id),
                    "status": "done",
                    "completed_at": now_dt.isoformat(),
                    "hide_after": hide_dt.isoformat()
                }
            })

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
            SELECT i.id, i.text, i.assigned_to_name, i.page_id
            FROM flipboard.items i
            JOIN flipboard.pages p ON i.page_id = p.id
            WHERE p.folder_id = :fid
              AND i.status = 'active'
            ORDER BY i.position ASC
        """), {"fid": folder_id})
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
            now_dt = datetime.utcnow()
            hide_dt = now_dt + timedelta(minutes=30)
            db.execute(text("""
                UPDATE flipboard.items
                SET status = 'done', completed_at = :cat, hide_after = :hat
                WHERE id = :tid
            """), {"tid": str(best_match[0]), "cat": now_dt, "hat": hide_dt})
            db.commit()

            # Broadcast real-time update to FlipBoard UI
            _broadcast_to_flipboard({
                "event": "item_done",
                "page_id": str(best_match[3]),
                "item": {
                    "id": str(best_match[0]),
                    "page_id": str(best_match[3]),
                    "status": "done",
                    "completed_at": now_dt.isoformat(),
                    "hide_after": hide_dt.isoformat()
                }
            })

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


async def _download_transcribe_and_parse_audio(media_id: str, phone: str, sender_name: str, db: Session) -> tuple[str, dict]:
    """Downloads audio from Meta and runs a single ultra-fast Gemini 2.5 Flash call
    that produces BOTH transcription and structured intent JSON simultaneously."""
    if not WHATSAPP_ACCESS_TOKEN or not GEMINI_API_KEY:
        return "[Voice Note Received]", {"intent": "general_chat", "text": "Voice note"}

    try:
        # Step 1: Get media URL from Meta & download audio
        media_info_url = f"https://graph.facebook.com/v19.0/{media_id}"
        headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get(media_info_url, headers=headers)
            if res.status_code != 200:
                return "[Voice Note Audio Unavailable]", {"intent": "general_chat"}
            download_url = res.json().get("url")

            res_audio = await client.get(download_url, headers=headers)
            if res_audio.status_code != 200:
                return "[Voice Note Download Error]", {"intent": "general_chat"}
            audio_bytes = res_audio.content

        # Step 2: Single unified Gemini call for transcription + intent parsing
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        prompt = f"""You are the AI Factory Assistant for Ground Up Factory in Pune, India (miso, vinegar, koji fermentation factory).
An employee ({sender_name}, phone {phone}) sent this voice note.

1. Transcribe the audio accurately in the original spoken language (Hindi, Hinglish, Marathi, Bengali, English).
2. Understand the intent and extract structured data:
- task_complete: worker finished a task ("done", "ho gaya", "saaf kiya", "cleaned")
- task_create / task_assignment: assigning a task to someone
- jar_check: inspecting or querying a jar ("jar 1", "check jar 42", ratings)
- status_query: asking what tasks/work is pending ("status", "aaj ke kaam", "kya pending hai")
- maintenance_update: maintenance started/ended for a room
- issue_report: reporting a problem, leak, damage, mold
- general_chat: greeting or other message

Return ONLY JSON:
{{
    "transcript": "exact transcription in original spoken language",
    "intent": "task_complete | task_create | jar_check | status_query | maintenance_update | issue_report | general_chat",
    "task_hint": "cleaned task description or title",
    "assigned_to": "target person if mentioned (Yadav Sir / Ravi / Priya / Owner)",
    "jar_number": null,
    "quality_details": {{"smell": "normal", "taste": "normal", "color": "normal"}},
    "quality_scores": {{"taste": null, "smell": null, "color": null}}
}}"""

        gem_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "parts": [
                    {"inline_data": {"mime_type": "audio/ogg", "data": audio_b64}},
                    {"text": prompt}
                ]
            }],
            "generationConfig": {"temperature": 0.1, "response_mime_type": "application/json"}
        }

        async with httpx.AsyncClient(timeout=12.0) as client:
            gem_res = await client.post(gem_url, json=payload)
            if gem_res.status_code == 200:
                result = gem_res.json()
                content_text = result["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(content_text.strip())
                transcript = parsed.get("transcript", "[Voice Note Transcribed]")
                logger.info(f"🎙️ Single-Pass Voice Note: '{transcript}' -> intent={parsed.get('intent')}")
                return transcript, parsed
    except Exception as e:
        logger.error(f"Error in unified voice note processing: {e}")

    return "[Voice Note Received]", {"intent": "general_chat"}


async def _download_and_transcribe_audio(media_id: str) -> str:
    transcript, _ = await _download_transcribe_and_parse_audio(media_id, "", "", None)
    return transcript


def _save_whatsapp_photo_file(image_bytes: bytes, ext: str = "jpg") -> str:
    """Saves raw photo bytes to web/uploads/ and returns the public media URL."""
    import uuid
    filename = f"wa_{uuid.uuid4().hex[:12]}.{ext}"
    
    possible_dirs = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web", "uploads"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "uploads"),
        r"C:\GroundUp\web\uploads",
        r"C:\GroundUp\ground-up-production\web\uploads",
        r"C:\GroundUp\ground-up-admin\web\uploads",
    ]
    
    for udir in possible_dirs:
        try:
            os.makedirs(udir, exist_ok=True)
            fpath = os.path.join(udir, filename)
            with open(fpath, "wb") as f:
                f.write(image_bytes)
        except Exception:
            pass
            
    return f"https://gu-production.initiativesewafoundation.com/media/file/{filename}"


async def _analyze_image(image_id: str, caption: str) -> str:
    """Fetch photo from Meta API, save to disk, and analyze with factory-aware Gemini Vision."""
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
            
            # Save photo to disk for timeline embedding
            photo_url = _save_whatsapp_photo_file(res_img.content, ext="jpg")
            img_b64 = base64.b64encode(res_img.content).decode("utf-8")

        prompt = f"""You are the AI assistant for Ground Up Factory in Pune, India — a food/fermentation factory making miso, vinegar, koji, and fermented products.
Analyze this photo sent by a worker on WhatsApp. Caption: '{caption}'

Categorize the photo and extract details:
1. is_qr_code: true if this photo is primarily a close-up scan/picture of a QR code sticker, QR label, or barcode on a jar/paper.
2. is_product: true if this photo shows the ACTUAL FOOD/PRODUCT/FERMENT (e.g. miso paste, koji grains, vinegar liquid, surface mold inspection, jar contents).
3. jar_number: integer jar number (e.g. 1, 42) if visible in the QR code, label, or caption.
4. analysis: 1-2 sentence direct description of the visual condition, color, texture, and fermentation quality.

Return ONLY JSON:
{{
    "is_qr_code": false,
    "is_product": true,
    "jar_number": 1,
    "analysis": "Light golden miso paste with smooth surface and rich texture. No mold observed."
}}"""

        gem_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "parts": [
                    {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}},
                    {"text": prompt}
                ]
            }],
            "generationConfig": {"temperature": 0.1, "response_mime_type": "application/json"}
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            gem_res = await client.post(gem_url, json=payload)
            if gem_res.status_code == 200:
                result_json = gem_res.json()
                raw_json = result_json["candidates"][0]["content"]["parts"][0]["text"].strip()
                try:
                    parsed = json.loads(raw_json)
                    analysis = parsed.get("analysis", "Visual inspection recorded.")
                    is_qr = "1" if parsed.get("is_qr_code") else "0"
                    jar_n = str(parsed.get("jar_number") or "")
                except Exception:
                    analysis = raw_json
                    is_qr = "0"
                    jar_n = ""

                logger.info(f"📸 Photo analyzed: QR={is_qr}, Jar={jar_n}, text={analysis[:80]}")
                return f"[PHOTO_ANALYSIS]{analysis}[/PHOTO_ANALYSIS][PHOTO_URL]{photo_url}[/PHOTO_URL][IS_QR]{is_qr}[/IS_QR][JAR_NUM]{jar_n}[/JAR_NUM] {caption}".strip()
    except Exception as e:
        logger.error(f"Error analyzing image: {e}")

    return caption or "[Photo Analyzed]"


# ─── Recipe & Production Handlers ────────────────────────────────────────────

async def _handle_recipe_query(question: str, sender_name: str, db: Session) -> str:
    """Answer recipe, SOP, and fermentation questions using Vector RAG Knowledge Base."""
    import re
    from groundup_webhooks.rag_engine import answer_with_rag, scale_recipe_formula

    lower = question.lower()
    
    # 1. Check if this is a recipe scaling request (e.g. "scale 50kg red miso", "batch 30kg white miso", "50kg miso")
    scale_match = re.search(r"(?:scale|batch|package|make)?\s*(\d+(?:\.\d+)?)\s*(?:kg|kilo|litres?|l)?\s+(?:of\s+)?(red miso|white miso|miso|shio koji|vinegar|sesame miso|seaweed miso|ginger beer)", lower)
    if not scale_match:
        scale_match = re.search(r"(red miso|white miso|shio koji|vinegar|sesame miso|seaweed miso|ginger beer)\s+(?:for\s+)?(\d+(?:\.\d+)?)\s*(?:kg|kilo)?", lower)
        if scale_match:
            prod = scale_match.group(1)
            qty = float(scale_match.group(2))
            return scale_recipe_formula(prod, qty)
    else:
        qty = float(scale_match.group(1))
        prod = scale_match.group(2)
        return scale_recipe_formula(prod, qty)

    # 2. General RAG Knowledge Query (Recipes, SOPs, Temperature, Mold, Hygiene)
    sender_info = _get_sender_info("", db) if not sender_name else {"preferred_lang": "en"}
    lang = sender_info.get("preferred_lang", "en")
    
    ans = answer_with_rag(user_query=question, sender_name=sender_name, preferred_lang=lang)
    return ans


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
            SELECT name, category, room, description
            FROM production.products
            WHERE active = true
            ORDER BY category, name
        """))
        products = result.fetchall()
        if products:
            lines = ["🏭 *Ground Up Product Catalog*", "━━━━━━━━━━━━━━━━━━━━"]
            by_cat = {}
            for p in products:
                cat = p[1] or "General"
                by_cat.setdefault(cat, []).append(f"• *{p[0]}*" + (f" ({p[2]})" if p[2] else ""))
            for cat, items in by_cat.items():
                lines.append(f"\n📦 *{cat.title()}:*")
                lines.extend(items)
            return "\n".join(lines)
    except Exception as e:
        logger.warning(f"Could not query production.products: {e}")
        try:
            db.rollback()
        except Exception:
            pass

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
        folder_id, folder_date_str = _get_today_daily_folder(db)
        res = db.execute(text("""
            SELECT i.status, COUNT(*)
            FROM flipboard.items i
            JOIN flipboard.pages p ON i.page_id = p.id
            WHERE p.folder_id = :fid AND i.status != 'deleted'
            GROUP BY i.status
        """), {"fid": folder_id}).fetchall()
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

