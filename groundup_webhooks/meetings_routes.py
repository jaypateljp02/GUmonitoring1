"""
meetings_routes.py
─────────────────────────────────────────────────────────────────
FastAPI routes for the Ground Up Meeting Hub.

Device endpoints (called by ESP32 firmware):
  POST /api/meetings/transcribe-chunk   — WAV → transcript
  POST /api/meetings/summarize-rolling  — transcript → rolling summary
  POST /api/meetings/complete           — full meeting data → store + WhatsApp

Admin/App endpoints (called by Meeting Hub web app):
  GET  /api/meetings/                   — list all meetings
  GET  /api/meetings/{id}               — single meeting detail
  POST /api/meetings/{id}/chat          — chat with a meeting
  PUT  /api/meetings/{id}/action-items  — update/approve action items
  POST /api/meetings/{id}/flipboard     — push one action item to Flipboard
  POST /api/meetings/{id}/whatsapp      — resend WhatsApp summary
  POST /api/meetings/{id}/regenerate    — regenerate summary from transcript

Auth:
  Device calls use X-Device-Key header (simple shared secret).
  App calls are open (no login required as per user spec).
─────────────────────────────────────────────────────────────────
"""
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Request, Response, Header, HTTPException, BackgroundTasks, UploadFile, File as FastAPIFile
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel

from groundup_webhooks.meetings_gemini import (
    transcribe_audio_chunk,
    generate_rolling_summary,
    generate_final_summary,
    chat_with_meeting,
)
from groundup_webhooks.meetings_db import (
    save_meeting,
    get_meetings,
    get_meeting,
    get_latest_meeting,
    get_total_meeting_stats,
    update_action_items,
    save_chat_message,
    get_chat_history,
    mark_whatsapp_sent,
    push_action_to_flipboard,
)

logger = logging.getLogger("groundup_webhooks.meetings_routes")

router = APIRouter(prefix="/api/meetings", tags=["Meeting Hub"])

# Shared secret burned into the ESP32 firmware
DEVICE_API_KEYS = ("groundup-meeting-recorder-2026", "gu-meeting-2026")

# Audio storage directory
AUDIO_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ground-up-meeting-hub", "web", "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)


# ─── Auth Helper ─────────────────────────────────────────────────────────────

def _verify_device_key(x_device_key: Optional[str]):
    if x_device_key not in DEVICE_API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid device key")


# ─── Pydantic Models ─────────────────────────────────────────────────────────

class RollingSummaryRequest(BaseModel):
    transcript_so_far: Optional[str] = None
    transcript: Optional[str] = None
    is_final: Optional[bool] = None


class CompleteMeetingRequest(BaseModel):
    device_id: str = "meeting-recorder-01"
    device_source: Optional[str] = "esp32_recorder"
    transcript: str
    duration_seconds: int = 0
    chunk_count: int = 0
    recorded_at: str = ""  # ISO 8601 string
    title: Optional[str] = None
    summary: Optional[str] = None


class ChatRequest(BaseModel):
    question: str


class ActionItemsRequest(BaseModel):
    action_items: list


class FlipboardRequest(BaseModel):
    action_item: dict  # {"task": "...", "assignee": "...", "due": null}


# ─── Device Endpoints ────────────────────────────────────────────────────────

@router.post("/transcribe-chunk")
async def transcribe_chunk(
    request: Request,
    x_device_key: Optional[str] = Header(None)
):
    """
    ESP32 sends a WAV file (multipart or raw body).
    Returns: {"transcript": "..."}
    Replaces the ElevenLabs Scribe call in the original firmware.
    """
    _verify_device_key(x_device_key)

    try:
        content_type = request.headers.get("content-type", "")
        wav_bytes = b""

        if "multipart/form-data" in content_type:
            form = await request.form()
            # The firmware sends boundary/form-data with field name "audio"
            audio_file = form.get("audio")
            if audio_file and hasattr(audio_file, "read"):
                wav_bytes = await audio_file.read()
            elif isinstance(audio_file, bytes):
                wav_bytes = audio_file
            else:
                # If "audio" field is missing but there is some other file/bytes part
                for key, value in form.items():
                    if hasattr(value, "read"):
                        wav_bytes = await value.read()
                        break
                    elif isinstance(value, bytes):
                        wav_bytes = value
                        break
        
        # Fallback to reading raw body
        if not wav_bytes:
            wav_bytes = await request.body()

        if not wav_bytes:
            return JSONResponse({"transcript": "[no audio received]"})

        logger.info(f"[Transcribe] Received {len(wav_bytes)} bytes from device")
        transcript = await transcribe_audio_chunk(wav_bytes)
        return JSONResponse({"transcript": transcript})

    except Exception as e:
        logger.error(f"[Transcribe] Error: {e}", exc_info=True)
        return JSONResponse({"transcript": "[transcription error]"}, status_code=500)


@router.post("/summarize-rolling")
async def summarize_rolling(
    body: RollingSummaryRequest,
    x_device_key: Optional[str] = Header(None)
):
    """
    Called after each chunk. Returns a rolling 2-3 sentence summary.
    Replaces the direct OpenAI call in the original firmware.
    """
    _verify_device_key(x_device_key)

    try:
        transcript = body.transcript_so_far or body.transcript or ""
        summary = await generate_rolling_summary(transcript)
        return JSONResponse({"summary": summary})
    except Exception as e:
        logger.error(f"[RollingSummary] Error: {e}")
        return JSONResponse({"summary": "Summary in progress..."})


@router.post("/complete")
async def complete_meeting(
    body: CompleteMeetingRequest,
    background_tasks: BackgroundTasks,
    x_device_key: Optional[str] = Header(None)
):
    """
    Called when the user presses STOP on the device.
    Stores the meeting and sends WhatsApp notification in the background.
    Returns: {"meeting_id": "...", "status": "ok"}
    """
    _verify_device_key(x_device_key)

    if not body.transcript or len(body.transcript) < 10:
        return JSONResponse({"status": "error", "message": "No transcript provided"}, status_code=400)

    recorded_at = body.recorded_at or datetime.utcnow().isoformat() + "Z"

    logger.info(f"[Complete] Meeting from {body.device_id} | {body.duration_seconds}s | {len(body.transcript)} chars")

    # Generate summary (may take up to 30s for long meetings)
    summary_dict = await generate_final_summary(body.transcript)

    # Save to DB
    meeting_id = save_meeting(
        transcript=body.transcript,
        summary_dict=summary_dict,
        duration_seconds=body.duration_seconds,
        recorded_at=recorded_at,
        chunk_count=body.chunk_count,
        device_id=body.device_id,
        device_source=body.device_source or "esp32_recorder",
        title=body.title,
    )

    if not meeting_id:
        return JSONResponse({"status": "error", "message": "Failed to save meeting"}, status_code=500)

    # Send WhatsApp notification in background (non-blocking)
    background_tasks.add_task(_notify_whatsapp, meeting_id, summary_dict, body.duration_seconds, recorded_at)

    return JSONResponse({
        "status": "ok",
        "meeting_id": meeting_id,
        "summary_preview": (summary_dict.get("overview", ""))[:200]
    })


# ─── Admin / App Endpoints ────────────────────────────────────────────────────

@router.get("/")
async def list_meetings(limit: int = 20, offset: int = 0):
    """Return list of meetings for the Meeting Hub dashboard."""
    meetings = get_meetings(limit=limit, offset=offset)
    stats = get_total_meeting_stats()
    return JSONResponse({
        "meetings": meetings,
        "count": len(meetings),
        "total_count": stats["total_meetings"],
        "total_seconds": stats["total_seconds"],
        "total_pending_actions": stats["total_pending_actions"],
    })


@router.get("/stats")
async def meeting_stats():
    """Return aggregate stats across ALL meetings."""
    stats = get_total_meeting_stats()
    return JSONResponse(stats)


@router.get("/{meeting_id}")
async def get_meeting_detail(meeting_id: str):
    """Return full meeting detail for the Meeting Hub detail view."""
    meeting = get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return JSONResponse(meeting)


@router.post("/{meeting_id}/chat")
async def chat_with_meeting_endpoint(meeting_id: str, body: ChatRequest):
    """Chat with a meeting's transcript. Maintains history per session."""
    meeting = get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    history = get_chat_history(meeting_id, limit=20)
    transcript = meeting.get("transcript", "")

    answer = await chat_with_meeting(transcript, body.question, history)

    # Persist conversation
    save_chat_message(meeting_id, "user", body.question)
    save_chat_message(meeting_id, "assistant", answer)

    return JSONResponse({"answer": answer})


@router.put("/{meeting_id}/action-items")
async def update_action_items_endpoint(meeting_id: str, body: ActionItemsRequest):
    """Update (approve / edit) action items from the Meeting Hub UI."""
    ok = update_action_items(meeting_id, body.action_items)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update action items")

    # Check for newly approved items and emit WhatsApp event to assignee
    try:
        meeting = get_meeting(meeting_id)
        m_title = meeting.get("title", "Meeting") if meeting else "Meeting"
        for item in body.action_items:
            if isinstance(item, dict) and item.get("approved"):
                from groundup_webhooks.event_bus import emit_event
                emit_event("meetings.action_items.approved", {
                    "assignee": item.get("assignee", "Worker"),
                    "task": item.get("task", "Action Item"),
                    "due": item.get("due", ""),
                    "meeting_title": m_title,
                    "meeting_id": meeting_id
                })
    except Exception as e:
        logger.warning(f"Failed to dispatch approved action items WA notification: {e}")

    return JSONResponse({"status": "ok"})


@router.post("/{meeting_id}/flipboard")
async def push_to_flipboard(meeting_id: str, body: FlipboardRequest):
    """Push one approved action item to the Ground Up Flipboard."""
    ok = push_action_to_flipboard(meeting_id, body.action_item)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to push to Flipboard")
    return JSONResponse({"status": "ok", "message": "Task added to Flipboard ✅"})


@router.post("/{meeting_id}/whatsapp")
async def resend_whatsapp(meeting_id: str, background_tasks: BackgroundTasks):
    """Resend the WhatsApp summary for a meeting to Group 1."""
    meeting = get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    summary_dict = {
        "overview": meeting.get("overview", ""),
        "decisions": meeting.get("decisions", []),
        "action_items": meeting.get("action_items", []),
    }
    duration = meeting.get("duration_seconds", 0)
    recorded_at = meeting.get("recorded_at", "")
    background_tasks.add_task(_notify_whatsapp, meeting_id, summary_dict, duration, recorded_at)
    return JSONResponse({"status": "ok", "message": "Sending WhatsApp..."})


@router.post("/{meeting_id}/regenerate")
async def regenerate_summary(meeting_id: str, background_tasks: BackgroundTasks):
    """Regenerate the AI summary from the saved transcript."""
    meeting = get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    transcript = meeting.get("transcript", "")
    if not transcript or len(transcript) < 50:
        raise HTTPException(status_code=400, detail="No transcript available")

    background_tasks.add_task(_regenerate_summary_bg, meeting_id, transcript)
    return JSONResponse({"status": "ok", "message": "Regenerating summary..."})


@router.post("/{meeting_id}/upload-audio")
async def upload_audio(
    meeting_id: str,
    audio: UploadFile = FastAPIFile(...),
):
    """Upload the full meeting audio recording. Saves to disk and updates DB."""
    meeting = get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Determine file extension from content type
    ext_map = {
        "audio/webm": ".webm",
        "audio/wav": ".wav",
        "audio/ogg": ".ogg",
        "audio/mp4": ".m4a",
        "audio/mpeg": ".mp3",
    }
    content_type = audio.content_type or "audio/webm"
    ext = ext_map.get(content_type, ".webm")
    filename = f"{meeting_id}{ext}"
    filepath = os.path.join(AUDIO_DIR, filename)

    # Save file to disk
    try:
        contents = await audio.read()
        with open(filepath, "wb") as f:
            f.write(contents)
        logger.info(f"[Audio] Saved {len(contents)} bytes → {filepath}")
    except Exception as e:
        logger.error(f"[Audio] Save failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to save audio file")

    # Update DB with audio path
    try:
        from groundup_webhooks.database import SessionLocal
        from sqlalchemy import text as sql_text
        db = SessionLocal()
        try:
            db.execute(sql_text(
                "UPDATE meetings.meeting SET audio_file_path = :path WHERE id = :id"
            ), {"path": filepath, "id": meeting_id})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.error(f"[Audio] DB update failed: {e}")

    return JSONResponse({"status": "ok", "filename": filename, "size_bytes": len(contents)})


@router.get("/{meeting_id}/audio")
async def serve_audio(meeting_id: str):
    """Stream the saved audio file for playback."""
    meeting = get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    if not meeting.get("has_audio"):
        raise HTTPException(status_code=404, detail="No audio recording for this meeting")

    # Find audio file (could be .webm, .wav, etc.)
    for ext in [".webm", ".wav", ".ogg", ".m4a", ".mp3"]:
        filepath = os.path.join(AUDIO_DIR, f"{meeting_id}{ext}")
        if os.path.exists(filepath):
            media_types = {
                ".webm": "audio/webm",
                ".wav": "audio/wav",
                ".ogg": "audio/ogg",
                ".m4a": "audio/mp4",
                ".mp3": "audio/mpeg",
            }
            return FileResponse(
                filepath,
                media_type=media_types.get(ext, "audio/webm"),
                filename=f"meeting_{meeting_id[:8]}{ext}",
            )

    raise HTTPException(status_code=404, detail="Audio file not found on disk")


# ─── Background Tasks ─────────────────────────────────────────────────────────

async def _notify_whatsapp(meeting_id: str, summary_dict: dict, duration_seconds: int, recorded_at: str):
    """Send meeting summary to Ground Up Group 1 via Bandhu WhatsApp bot."""
    try:
        from groundup_webhooks.database import SessionLocal
        from groundup_webhooks.models import WebhookRecipient
        from groundup_webhooks.whatsapp_client import send_whatsapp_text

        # Format duration
        mins = duration_seconds // 60
        dur_str = f"{mins} min" if mins > 0 else "< 1 min"

        # Format recorded time
        try:
            dt = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
            ist = dt + timedelta(hours=5, minutes=30)
            time_str = ist.strftime("%d %b %Y, %I:%M %p")
        except Exception:
            time_str = "Today"

        # Build message
        lines = [f"🎙️ *Meeting Recorded*", f"Ground Up | {time_str} | {dur_str}", ""]

        overview = summary_dict.get("overview", "")
        if overview:
            lines.append(f"📋 *Overview*\n{overview[:300]}")
            lines.append("")

        decisions = summary_dict.get("decisions", [])
        if decisions:
            lines.append("✅ *Decisions*")
            for d in decisions[:5]:
                lines.append(f"• {d}")
            lines.append("")

        action_items = summary_dict.get("action_items", [])
        if action_items:
            lines.append("📌 *Action Items*")
            for ai in action_items[:5]:
                if isinstance(ai, dict):
                    task = ai.get("task", "")
                    assignee = ai.get("assignee", "")
                    due = ai.get("due", "")
                    item = f"• {assignee} → {task}" if assignee else f"• {task}"
                    if due:
                        item += f" (by {due})"
                    lines.append(item)
            lines.append("")

        hub_url = f"https://gubandhu.initiativesewafoundation.com/meetings/#{meeting_id}"
        lines.append(f"🔗 _View full meeting & chat: {hub_url}_")

        message = "\n".join(lines)

        # Send to all Group 1 recipients
        db = SessionLocal()
        try:
            recipients = db.query(WebhookRecipient).filter(
                WebhookRecipient.is_active == True,
                WebhookRecipient.alert_group == 1
            ).all()
            for r in recipients:
                await send_whatsapp_text(r.phone_number, message, db, sender_name="Bandhu")
        finally:
            db.close()

        mark_whatsapp_sent(meeting_id)
        logger.info(f"✅ WhatsApp meeting summary sent for {meeting_id}")

    except Exception as e:
        logger.error(f"[WhatsApp notify] Error: {e}", exc_info=True)


async def _regenerate_summary_bg(meeting_id: str, transcript: str):
    """Background task: regenerate summary and update DB."""
    try:
        from groundup_webhooks.database import SessionLocal
        from sqlalchemy import text
        import json

        summary_dict = await generate_final_summary(transcript)

        db = SessionLocal()
        try:
            db.execute(text("""
                UPDATE meetings.meeting SET
                    overview = :overview,
                    key_points = CAST(:key_points AS jsonb),
                    decisions = CAST(:decisions AS jsonb),
                    action_items = CAST(:action_items AS jsonb),
                    names_dates = :names_dates,
                    raw_summary = :raw_summary
                WHERE id = :id
            """), {
                "id": meeting_id,
                "overview": summary_dict.get("overview", ""),
                "key_points": json.dumps(summary_dict.get("key_points", [])),
                "decisions": json.dumps(summary_dict.get("decisions", [])),
                "action_items": json.dumps(summary_dict.get("action_items", [])),
                "names_dates": summary_dict.get("names_dates", ""),
                "raw_summary": summary_dict.get("raw_summary", ""),
            })
            db.commit()
            logger.info(f"✅ Summary regenerated for {meeting_id}")
        finally:
            db.close()
    except Exception as e:
        logger.error(f"[Regenerate] Error: {e}", exc_info=True)
