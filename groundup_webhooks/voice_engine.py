import os
import re
import uuid
import base64
import logging
from typing import Optional, Tuple, Dict, Any
import httpx
from sqlalchemy.orm import Session

from groundup_webhooks.config import (
    GEMINI_API_KEY,
    META_API_VERSION,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_ACCESS_TOKEN
)
from groundup_webhooks.circuit_breaker import is_circuit_open, record_success, record_failure
from groundup_webhooks.models import WhatsAppMessage

logger = logging.getLogger("groundup_webhooks.voice_engine")

API_URL = f"https://graph.facebook.com/{META_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
    "Content-Type": "application/json",
}


def clean_text_for_speech(raw_markdown: str) -> str:
    """Strip markdown symbols, urls, and excessive emojis for clean TTS audio synthesis."""
    # Remove markdown bold/italic
    text = re.sub(r"[\*_~`#]", "", raw_markdown)
    # Remove divider lines
    text = re.sub(r"━+", " ", text)
    text = re.sub(r"-{3,}", " ", text)
    # Remove URLs
    text = re.sub(r"https?://\S+", "", text)
    # Remove emojis that sound awkward
    text = re.sub(r"[👉📱📸📦🍲🏺🪣⚖️⏳👃👅🎨📊🚨🔴🟢⚠️✅👍🎙️💡ℹ️📋🛒🏭]", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def synthesize_speech(text_content: str, language: str = "en") -> Optional[Tuple[str, str]]:
    """
    Synthesizes speech audio using gTTS and saves to public web/uploads/audio/
    Returns (local_file_path, public_url).
    """
    try:
        from gtts import gTTS
    except ImportError:
        logger.error("gTTS is not installed.")
        return None

    clean_text = clean_text_for_speech(text_content)
    if not clean_text or len(clean_text) < 2:
        return None

    # Truncate very long texts to ~120 words for fast audio note generation
    words = clean_text.split()
    if len(words) > 120:
        clean_text = " ".join(words[:120]) + "..."

    # Map language code to gTTS supported tags
    lang_map = {
        "hi": "hi",
        "mr": "mr",
        "bn": "bn",
        "en": "en",
        "hinglish": "hi"
    }
    tts_lang = lang_map.get(language.lower(), "hi" if any(w in clean_text.lower() for w in ["hai", "karo", "kya", "namaste", "saaf"]) else "en")

    filename = f"vn_{uuid.uuid4().hex[:12]}.mp3"

    possible_dirs = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web", "uploads", "audio"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "uploads", "audio"),
        r"C:\GroundUp\web\uploads\audio",
        r"C:\GroundUp\ground-up-production\web\uploads\audio",
        r"C:\GroundUp\ground-up-admin\web\uploads\audio",
    ]

    saved_path = None
    for udir in possible_dirs:
        try:
            os.makedirs(udir, exist_ok=True)
            fpath = os.path.join(udir, filename)
            tts = gTTS(text=clean_text, lang=tts_lang, slow=False)
            tts.save(fpath)
            saved_path = fpath
            logger.info(f"🎙️ Voice note synthesized ({tts_lang}): {fpath}")
            break
        except Exception as e:
            logger.warning(f"Could not save TTS to {udir}: {e}")

    if not saved_path:
        return None

    public_url = f"https://gu-production.initiativesewafoundation.com/media/file/audio/{filename}"
    return saved_path, public_url


async def send_whatsapp_audio(
    phone_number: str,
    audio_url: str,
    db: Session,
    sender_name: str = "GroundUp Bot"
) -> bool:
    """Sends native audio voice note via Meta WhatsApp Cloud API."""
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        logger.error("WhatsApp credentials missing.")
        return False

    if is_circuit_open(db):
        logger.warning(f"Circuit open: Skipping audio to {phone_number}")
        return False

    clean_phone = phone_number.replace("+", "").replace(" ", "").strip()
    payload = {
        "messaging_product": "whatsapp",
        "to": clean_phone,
        "type": "audio",
        "audio": {"link": audio_url}
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(API_URL, json=payload, headers=HEADERS)
            if res.status_code in (200, 201):
                record_success(db)
                msg = WhatsAppMessage(
                    direction="outgoing",
                    phone_number=clean_phone,
                    sender_name=sender_name,
                    message_type="audio",
                    raw_content=f"[Voice Note: {audio_url}]",
                    status="sent"
                )
                db.add(msg)
                db.commit()
                logger.info(f"🎙️ WhatsApp voice note sent to {clean_phone} -> {audio_url}")
                return True
            else:
                err = f"API {res.status_code}: {res.text}"
                logger.error(f"❌ WhatsApp audio send failed to {clean_phone}: {err}")
                record_failure(db, err)
                return False
    except Exception as e:
        logger.error(f"❌ WhatsApp audio send exception: {e}")
        record_failure(db, str(e))
        return False
