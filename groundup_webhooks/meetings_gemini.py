"""
meetings_gemini.py
─────────────────────────────────────────────────────────────────
All Gemini AI calls for the Meeting Hub:
  • transcribe_audio_chunk() — WAV bytes → transcript text
  • generate_rolling_summary() — running summary during recording
  • generate_final_summary() — full structured summary at end
  • chat_with_meeting() — grounded Q&A against transcript

Uses the same GEMINI_API_KEY already in config.py.
No ElevenLabs. No OpenAI. Gemini handles everything.
─────────────────────────────────────────────────────────────────
"""
import base64
import logging
import json
import httpx

from groundup_webhooks.config import GEMINI_API_KEY

logger = logging.getLogger("groundup_webhooks.meetings_gemini")

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
FLASH_MODEL = "gemini-2.5-flash"

HEADERS = {"Content-Type": "application/json"}


# ─── Audio Format Detection ──────────────────────────────────────────────────

def _detect_audio_mime(audio_bytes: bytes) -> str:
    """
    Detect the actual audio format from file magic bytes.
    Browser MediaRecorder typically sends WebM/Opus, NOT WAV.
    ESP32 sends real WAV files. Getting this wrong causes Gemini
    to hallucinate completely wrong transcripts.
    """
    if not audio_bytes or len(audio_bytes) < 12:
        return "audio/wav"  # safe fallback

    # WAV: starts with "RIFF" ... "WAVE"
    if audio_bytes[:4] == b'RIFF' and audio_bytes[8:12] == b'WAVE':
        return "audio/wav"

    # WebM: starts with 0x1A 0x45 0xDF 0xA3 (EBML header)
    if audio_bytes[:4] == b'\x1a\x45\xdf\xa3':
        return "audio/webm"

    # Ogg/Opus: starts with "OggS"
    if audio_bytes[:4] == b'OggS':
        return "audio/ogg"

    # MP3: starts with 0xFF 0xFB or ID3 tag
    if audio_bytes[:3] == b'ID3' or (audio_bytes[0] == 0xFF and (audio_bytes[1] & 0xE0) == 0xE0):
        return "audio/mp3"

    # FLAC
    if audio_bytes[:4] == b'fLaC':
        return "audio/flac"

    # M4A/AAC: starts with "ftyp" at offset 4
    if len(audio_bytes) >= 8 and audio_bytes[4:8] == b'ftyp':
        return "audio/mp4"

    logger.warning(f"[AudioDetect] Unknown format, first 8 bytes: {audio_bytes[:8].hex()}")
    return "audio/wav"  # fallback


# ─── Audio Transcription ─────────────────────────────────────────────────────

async def transcribe_audio_chunk(audio_bytes: bytes) -> str:
    """
    Send an audio chunk to Gemini 2.5 Flash and return the transcript.
    Auto-detects the audio format (WAV from ESP32, WebM from browser,
    OGG/MP4 from file uploads) so Gemini always gets the correct mime type.
    """
    if not audio_bytes:
        return ""

    mime_type = _detect_audio_mime(audio_bytes)
    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")

    logger.info(f"[Transcribe] {len(audio_bytes)} bytes, detected format: {mime_type}")

    payload = {
        "contents": [{
            "parts": [
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": audio_b64,
                    }
                },
                {
                    "text": (
                        "Transcribe this audio recording exactly as spoken. "
                        "Include all speakers. Do not summarise. "
                        "Return only the transcript text, nothing else. "
                        "If no speech is detected, return exactly: [no speech detected]"
                    )
                }
            ]
        }],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 2048,
        }
    }

    url = f"{GEMINI_BASE}/models/{FLASH_MODEL}:generateContent?key={GEMINI_API_KEY}"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            res = await client.post(url, json=payload, headers=HEADERS)
            if res.status_code == 200:
                data = res.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                logger.info(f"[Transcribe] {len(text)} chars transcribed")
                return text if text else "[no speech detected]"
            else:
                logger.error(f"[Transcribe] Gemini HTTP {res.status_code}: {res.text[:200]}")
                return "[transcription failed]"
    except Exception as e:
        logger.error(f"[Transcribe] Error: {e}")
        return "[transcription error]"


# ─── Rolling Summary (per-chunk, during meeting) ──────────────────────────────

async def generate_rolling_summary(transcript_so_far: str) -> str:
    """
    Generate a 2–3 sentence rolling summary of the meeting so far.
    Called after each chunk is transcribed, same as the original rolling summary.
    """
    if not transcript_so_far or len(transcript_so_far.strip()) < 10:
        return "Meeting in progress..."

    prompt = (
        "You are summarising a meeting in progress for Ground Up Factory, Pune.\n"
        f"Here is the transcript so far:\n\n{transcript_so_far[:8000]}\n\n"
        "Write 2–3 sentences summarising what has been discussed so far. "
        "Be factual. Use present tense. Do not make up anything."
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 300}
    }

    url = f"{GEMINI_BASE}/models/{FLASH_MODEL}:generateContent?key={GEMINI_API_KEY}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(url, json=payload, headers=HEADERS)
            if res.status_code == 200:
                return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logger.warning(f"[RollingSummary] Error: {e}")

    return "Summary generation in progress..."


# ─── Final Structured Summary (at meeting end) ────────────────────────────────

async def generate_final_summary(full_transcript: str) -> dict:
    """
    Generate a structured multi-section summary at meeting end.
    Returns a dict with: overview, key_points, decisions, action_items, names_dates.

    For very long transcripts (4h+ meetings), uses map-reduce:
    split into 25KB segments → summarise each → merge.
    """
    if not full_transcript or len(full_transcript) < 50:
        return {
            "overview": "No content to summarise.",
            "key_points": [], "decisions": [], "action_items": [], "names_dates": "",
            "raw_summary": ""
        }

    # For transcripts under 200KB, use single call (covers ~4 hours of speech)
    if len(full_transcript) <= 200_000:
        return await _final_summary_single(full_transcript)
    else:
        return await _final_summary_map_reduce(full_transcript)


async def _final_summary_single(transcript: str) -> dict:
    """Single-call structured summary — best quality."""

    prompt = f"""You are the meeting intelligence assistant for Ground Up Factory, Pune, India.
A meeting was just recorded. Here is the full transcript:

{transcript[:180000]}

Generate a structured meeting summary as a JSON object with these exact keys:
- "overview": 3-4 sentence executive summary of what the meeting was about
- "key_points": array of strings, key discussion points (max 8)
- "decisions": array of strings, firm decisions made (can be empty)
- "action_items": array of objects with "task" (string), "assignee" (name or "Team"), "due" (date string or null)
- "names_dates_numbers": important names, dates, and numbers mentioned

Return ONLY the JSON object, no markdown, no explanation."""

    url = f"{GEMINI_BASE}/models/{FLASH_MODEL}:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 8192,
            "response_mime_type": "application/json"
        }
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            res = await client.post(url, json=payload, headers=HEADERS)
            if res.status_code == 200:
                raw = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                # Clean markdown fences if Gemini wraps JSON in ```json ... ```
                clean_raw = raw
                if "```" in clean_raw:
                    lines = [line for line in clean_raw.split("\n") if not line.strip().startswith("```")]
                    clean_raw = "\n".join(lines).strip()
                
                data = json.loads(clean_raw)
                return {
                    "overview": data.get("overview", ""),
                    "key_points": data.get("key_points", []),
                    "decisions": data.get("decisions", []),
                    "action_items": data.get("action_items", []),
                    "names_dates": data.get("names_dates_numbers", "") or data.get("names_dates", ""),
                    "raw_summary": raw,
                }
    except Exception as e:
        logger.error(f"[FinalSummary] Error: {e}", exc_info=True)

    return {
        "overview": "Summary generation failed. Please use Regenerate.",
        "key_points": [], "decisions": [], "action_items": [], "names_dates": "",
        "raw_summary": ""
    }


async def _final_summary_map_reduce(transcript: str) -> dict:
    """
    Map-reduce for very long meetings (>200KB transcript).
    Split into 25KB segments → summarise each → merge summaries.
    """
    SEGMENT_SIZE = 25_000
    segments = [transcript[i:i+SEGMENT_SIZE] for i in range(0, len(transcript), SEGMENT_SIZE)]
    logger.info(f"[MapReduce] {len(segments)} segments")

    segment_summaries = []
    for i, seg in enumerate(segments):
        prompt = (
            f"Summarise this section of a meeting transcript (segment {i+1}/{len(segments)}):\n\n{seg}\n\n"
            "Return a 3-5 sentence summary of this section only. Be factual."
        )
        url = f"{GEMINI_BASE}/models/{FLASH_MODEL}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 500}
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                res = await client.post(url, json=payload, headers=HEADERS)
                if res.status_code == 200:
                    s = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                    segment_summaries.append(s)
        except Exception as e:
            logger.warning(f"[MapReduce] Segment {i+1} failed: {e}")

    merged = "\n\n".join(segment_summaries)
    return await _final_summary_single(merged)


# ─── Chat with Meeting ────────────────────────────────────────────────────────

async def chat_with_meeting(transcript: str, question: str, history: list) -> str:
    """
    Grounded Q&A against a meeting transcript.
    history = [{"role": "user/assistant", "content": "..."}]
    Returns the assistant's reply.
    """
    if not transcript:
        return "I don't have the meeting transcript to answer from."

    # Build conversation contents
    contents = []

    # System context as first user turn
    system_text = (
        f"You are Bandhu, the AI assistant for Ground Up Factory, Pune.\n"
        f"Answer questions ONLY based on the following meeting transcript. "
        f"If the answer is not in the transcript, say so clearly.\n\n"
        f"MEETING TRANSCRIPT:\n{transcript[:150_000]}\n\n"
        f"Now answer the user's question:"
    )
    contents.append({"role": "user", "parts": [{"text": system_text}]})
    contents.append({"role": "model", "parts": [{"text": "Understood. I will answer based only on the transcript."}]})

    # Add conversation history
    for msg in history[-8:]:  # keep last 8 turns
        role = "model" if msg["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})

    # Add current question
    contents.append({"role": "user", "parts": [{"text": question}]})

    url = f"{GEMINI_BASE}/models/{FLASH_MODEL}:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": contents,
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024}
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(url, json=payload, headers=HEADERS)
            if res.status_code == 200:
                return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            logger.error(f"[Chat] Gemini HTTP {res.status_code}: {res.text[:200]}")
    except Exception as e:
        logger.error(f"[Chat] Error: {e}")

    return "Sorry, I couldn't process that right now. Please try again."
