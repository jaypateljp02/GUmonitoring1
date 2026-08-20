"""
meetings_db.py
─────────────────────────────────────────────────────────────────
Database helpers for the Meeting Hub.
Uses the same PostgreSQL database (groundupfactory) with a new
`meetings` schema — consistent with how monitoring, tasks, webhooks
schemas are isolated.
─────────────────────────────────────────────────────────────────
"""
import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import text
from groundup_webhooks.database import SessionLocal

logger = logging.getLogger("groundup_webhooks.meetings_db")

# ─── Schema Bootstrap ────────────────────────────────────────────────────────

CREATE_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS meetings;

CREATE TABLE IF NOT EXISTS meetings.meeting (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id        TEXT NOT NULL DEFAULT 'meeting-recorder-01',
    title            TEXT,
    transcript       TEXT,
    overview         TEXT,
    key_points       JSONB,
    decisions        JSONB,
    action_items     JSONB,
    names_dates      TEXT,
    raw_summary      TEXT,
    duration_seconds INTEGER,
    chunk_count      INTEGER,
    recorded_at      TIMESTAMPTZ,
    device_source    TEXT DEFAULT 'esp32_recorder',
    status           TEXT DEFAULT 'complete',
    whatsapp_sent    BOOLEAN DEFAULT FALSE,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE meetings.meeting ADD COLUMN IF NOT EXISTS device_source TEXT DEFAULT 'esp32_recorder';
ALTER TABLE meetings.meeting ADD COLUMN IF NOT EXISTS audio_file_path TEXT;

CREATE TABLE IF NOT EXISTS meetings.chat_message (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    meeting_id  UUID REFERENCES meetings.meeting(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_meetings_recorded_at ON meetings.meeting (recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_meeting_id ON meetings.chat_message (meeting_id, created_at);
"""


def ensure_schema():
    """Create meetings schema and tables if they don't exist. Call at app startup."""
    db = SessionLocal()
    try:
        db.execute(text(CREATE_SCHEMA_SQL))
        db.commit()
        logger.info("✅ meetings schema ready")
    except Exception as e:
        logger.error(f"meetings schema setup failed: {e}")
        db.rollback()
    finally:
        db.close()


# ─── Meeting CRUD ─────────────────────────────────────────────────────────────

def save_meeting(
    transcript: str,
    summary_dict: dict,
    duration_seconds: int,
    recorded_at: str,
    chunk_count: int = 0,
    device_id: str = "meeting-recorder-01",
    device_source: str = "esp32_recorder",
    title: str = None,
    audio_file_path: str = None,
) -> Optional[str]:
    """
    Persist a completed meeting to the DB.
    summary_dict should have keys: overview, key_points, decisions, action_items, names_dates
    Returns the UUID string of the saved meeting, or None on failure.
    """
    db = SessionLocal()
    try:
        meeting_id = str(uuid.uuid4())

        # Build auto title from timestamp
        if not title:
            try:
                dt = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
                ist = dt + timedelta(hours=5, minutes=30)
                title = f"Ground Up Meeting — {ist.strftime('%d %b %Y, %I:%M %p')}"
            except Exception:
                title = f"Meeting — {recorded_at[:10]}"

        # Wrap action_items to add approved/flipboard flags
        action_items = summary_dict.get("action_items", [])
        if isinstance(action_items, list):
            for ai in action_items:
                if isinstance(ai, dict):
                    ai.setdefault("approved", False)
                    ai.setdefault("added_to_flipboard", False)

        import json
        db.execute(text("""
            INSERT INTO meetings.meeting (
                id, device_id, device_source, title, transcript,
                overview, key_points, decisions, action_items, names_dates, raw_summary,
                duration_seconds, chunk_count, recorded_at, status, audio_file_path
            ) VALUES (
                :id, :device_id, :device_source, :title, :transcript,
                :overview, CAST(:key_points AS jsonb), CAST(:decisions AS jsonb), CAST(:action_items AS jsonb), :names_dates, :raw_summary,
                :duration_seconds, :chunk_count, CAST(:recorded_at AS timestamptz), 'complete', :audio_file_path
            )
        """), {
            "id": meeting_id,
            "device_id": device_id,
            "device_source": device_source or "esp32_recorder",
            "title": title,
            "transcript": transcript,
            "overview": summary_dict.get("overview", ""),
            "key_points": json.dumps(summary_dict.get("key_points", [])),
            "decisions": json.dumps(summary_dict.get("decisions", [])),
            "action_items": json.dumps(action_items),
            "names_dates": summary_dict.get("names_dates", ""),
            "raw_summary": summary_dict.get("raw_summary", ""),
            "duration_seconds": duration_seconds,
            "chunk_count": chunk_count,
            "recorded_at": recorded_at,
            "audio_file_path": audio_file_path,
        })
        db.commit()
        logger.info(f"✅ Meeting saved: {meeting_id} | {title} | Source: {device_source}")
        return meeting_id
    except Exception as e:
        logger.error(f"save_meeting failed: {e}", exc_info=True)
        db.rollback()
        return None
    finally:
        db.close()


def get_meetings(limit: int = 20, offset: int = 0) -> list:
    """Return list of meetings (most recent first) for the dashboard."""
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT id, title, overview, duration_seconds, chunk_count,
                   recorded_at, status, whatsapp_sent, action_items,
                   decisions, device_source, created_at, audio_file_path
            FROM meetings.meeting
            ORDER BY recorded_at DESC
            LIMIT :limit OFFSET :offset
        """), {"limit": limit, "offset": offset}).fetchall()

        result = []
        for r in rows:
            action_items = r[8] or []
            pending = sum(1 for ai in action_items if isinstance(ai, dict) and not ai.get("approved", False))
            result.append({
                "id": str(r[0]),
                "title": r[1],
                "overview": (r[2] or "")[:200],
                "duration_seconds": r[3] or 0,
                "chunk_count": r[4] or 0,
                "recorded_at": r[5].isoformat() if r[5] else None,
                "status": r[6],
                "whatsapp_sent": r[7],
                "decisions_count": len(r[9] or []),
                "action_items_count": len(action_items),
                "pending_actions": pending,
                "device_source": r[10] or "esp32_recorder",
                "has_audio": bool(r[12]),
            })
        return result
    except Exception as e:
        logger.error(f"get_meetings failed: {e}")
        return []
    finally:
        db.close()


def get_total_meeting_stats() -> dict:
    """Return aggregate stats across ALL meetings for the dashboard."""
    db = SessionLocal()
    try:
        r = db.execute(text("""
            SELECT
                COUNT(*) AS total_meetings,
                COALESCE(SUM(duration_seconds), 0) AS total_seconds,
                COALESCE(SUM(
                    jsonb_array_length(
                        CASE WHEN jsonb_typeof(action_items) = 'array' THEN action_items ELSE '[]'::jsonb END
                    )
                ), 0) AS total_actions
            FROM meetings.meeting
        """)).fetchone()
        
        # Count pending (unapproved) action items separately
        pending_r = db.execute(text("""
            SELECT COALESCE(SUM(cnt), 0) FROM (
                SELECT COUNT(*) AS cnt FROM meetings.meeting,
                    jsonb_array_elements(
                        CASE WHEN jsonb_typeof(action_items) = 'array' THEN action_items ELSE '[]'::jsonb END
                    ) AS ai
                WHERE (ai->>'approved')::boolean IS NOT TRUE
            ) sub
        """)).fetchone()

        return {
            "total_meetings": int(r[0]) if r and r[0] is not None else 0,
            "total_seconds": int(r[1]) if r and r[1] is not None else 0,
            "total_pending_actions": int(pending_r[0]) if pending_r and pending_r[0] is not None else 0,
        }
    except Exception as e:
        logger.error(f"get_total_meeting_stats failed: {e}")
        return {"total_meetings": 0, "total_seconds": 0, "total_pending_actions": 0}
    finally:
        db.close()


def get_meeting(meeting_id: str) -> Optional[dict]:
    """Return full meeting detail including transcript, summary, action items."""
    db = SessionLocal()
    try:
        r = db.execute(text("""
            SELECT id, device_id, title, transcript,
                   overview, key_points, decisions, action_items, names_dates,
                   duration_seconds, chunk_count, recorded_at, status, whatsapp_sent,
                   device_source, created_at, audio_file_path
            FROM meetings.meeting
            WHERE id = :id
        """), {"id": meeting_id}).fetchone()

        if not r:
            return None

        return {
            "id": str(r[0]),
            "device_id": r[1],
            "title": r[2],
            "transcript": r[3],
            "overview": r[4],
            "key_points": r[5] or [],
            "decisions": r[6] or [],
            "action_items": r[7] or [],
            "names_dates": r[8],
            "duration_seconds": r[9] or 0,
            "chunk_count": r[10] or 0,
            "recorded_at": r[11].isoformat() if r[11] else None,
            "status": r[12],
            "whatsapp_sent": r[13],
            "device_source": r[14] or "esp32_recorder",
            "created_at": r[15].isoformat() if r[15] else None,
            "has_audio": bool(r[16]),
            "audio_url": f"/api/meetings/{meeting_id}/audio" if r[16] else None,
        }
    except Exception as e:
        logger.error(f"get_meeting failed: {e}")
        return None
    finally:
        db.close()


def get_latest_meeting() -> Optional[dict]:
    """Return the most recent meeting (used by WhatsApp Bandhu queries)."""
    db = SessionLocal()
    try:
        r = db.execute(text("""
            SELECT id FROM meetings.meeting
            ORDER BY recorded_at DESC LIMIT 1
        """)).fetchone()
        if not r:
            return None
        return get_meeting(str(r[0]))
    finally:
        db.close()


def update_action_items(meeting_id: str, action_items: list) -> bool:
    """Update action items for a meeting (used by Meeting Hub UI)."""
    db = SessionLocal()
    try:
        import json
        db.execute(text("""
            UPDATE meetings.meeting
            SET action_items = CAST(:action_items AS jsonb)
            WHERE id = :id
        """), {"id": meeting_id, "action_items": json.dumps(action_items)})
        db.commit()
        return True
    except Exception as e:
        logger.error(f"update_action_items failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()


def mark_whatsapp_sent(meeting_id: str):
    """Mark meeting as having been sent to WhatsApp."""
    db = SessionLocal()
    try:
        db.execute(text("UPDATE meetings.meeting SET whatsapp_sent = TRUE WHERE id = :id"), {"id": meeting_id})
        db.commit()
    except Exception as e:
        logger.warning(f"mark_whatsapp_sent failed: {e}")
    finally:
        db.close()


# ─── Chat History ────────────────────────────────────────────────────────────

def save_chat_message(meeting_id: str, role: str, content: str):
    """Persist one chat turn (role = 'user' or 'assistant')."""
    db = SessionLocal()
    try:
        db.execute(text("""
            INSERT INTO meetings.chat_message (id, meeting_id, role, content)
            VALUES (:id, :meeting_id, :role, :content)
        """), {"id": str(uuid.uuid4()), "meeting_id": meeting_id, "role": role, "content": content})
        db.commit()
    except Exception as e:
        logger.warning(f"save_chat_message failed: {e}")
    finally:
        db.close()


def get_chat_history(meeting_id: str, limit: int = 20) -> list:
    """Return recent chat messages for a meeting."""
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT role, content, created_at FROM meetings.chat_message
            WHERE meeting_id = :id
            ORDER BY created_at ASC
            LIMIT :limit
        """), {"id": meeting_id, "limit": limit}).fetchall()
        return [{"role": r[0], "content": r[1], "created_at": r[2].isoformat() if r[2] else None} for r in rows]
    except Exception as e:
        logger.warning(f"get_chat_history failed: {e}")
        return []
    finally:
        db.close()


# ─── Flipboard Integration ───────────────────────────────────────────────────

def push_action_to_flipboard(meeting_id: str, action_item: dict) -> bool:
    """
    Push an approved action item to the Ground Up Flipboard.
    Reuses _sync_task_to_flipboard logic directly via SQL.
    """
    db = SessionLocal()
    try:
        import uuid as _uuid
        from datetime import datetime, timedelta

        title = action_item.get("task", "Meeting action item")
        assigned_to = action_item.get("assignee", "Staff")
        today_str = (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")

        # Get or create today's Daily Work folder
        folder_res = db.execute(text("""
            SELECT id FROM flipboard.folders
            WHERE folder_type = 'daily_auto' AND title LIKE :pat
            ORDER BY created_at DESC LIMIT 1
        """), {"pat": f"Daily Work - {today_str}%"}).fetchone()

        if not folder_res:
            fid = str(_uuid.uuid4())
            db.execute(text("""
                INSERT INTO flipboard.folders (id, title, folder_type, created_at)
                VALUES (:fid, :title, 'daily_auto', NOW())
            """), {"fid": fid, "title": f"Daily Work - {today_str}"})
            db.commit()
            folder_id = fid
        else:
            folder_id = folder_res[0]

        # Get or create page 1
        page_res = db.execute(text("""
            SELECT id FROM flipboard.pages WHERE folder_id = :fid AND page_number = 1 LIMIT 1
        """), {"fid": folder_id}).fetchone()

        if not page_res:
            pid = str(_uuid.uuid4())
            db.execute(text("""
                INSERT INTO flipboard.pages (id, folder_id, page_number, page_date, created_at)
                VALUES (:pid, :fid, 1, CURRENT_DATE, NOW())
            """), {"pid": pid, "fid": folder_id})
            db.commit()
            page_id = pid
        else:
            page_id = page_res[0]

        pos_res = db.execute(text(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM flipboard.items WHERE page_id = :pid"
        ), {"pid": page_id}).fetchone()
        next_pos = pos_res[0] if pos_res else 1

        item_id = str(_uuid.uuid4())
        db.execute(text("""
            INSERT INTO flipboard.items (id, page_id, text, assigned_to_name, position, priority, status, source, created_at)
            VALUES (:iid, :pid, :text, :assigned, :pos, 'normal', 'active', 'meeting', NOW())
        """), {"iid": item_id, "pid": page_id, "text": title, "assigned": assigned_to, "pos": next_pos})
        db.commit()

        logger.info(f"📋 Action item pushed to Flipboard: '{title}' → {assigned_to}")
        return True

    except Exception as e:
        logger.error(f"push_action_to_flipboard failed: {e}", exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()
