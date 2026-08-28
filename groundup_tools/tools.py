import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from .registry import register_tool
from groundup_webhooks.rag_engine import search_knowledge, scale_recipe_formula
from groundup_webhooks.voice_engine import synthesize_speech
from groundup_webhooks.event_bus import emit_event

logger = logging.getLogger("groundup_tools.tools")


# ─── 1. IoT & Environment Tools ───────────────────────────────────────────────

@register_tool(
    name="iot_get_live_telemetry",
    description="Retrieves live temperature, humidity, power draw (Watts), and voltage for factory rooms and fridges.",
    parameters_schema={
        "type": "object",
        "properties": {
            "room_or_device": {
                "type": "string",
                "description": "Name of room or equipment (e.g. 'Miso room', 'Vinegar room', 'Hall fridge', 'Black fridge', 'Terrace fridge', 'all')"
            }
        },
        "required": []
    }
)
def iot_get_live_telemetry(room_or_device: str = "all", db: Optional[Session] = None) -> Dict[str, Any]:
    if not db:
        from groundup_webhooks.database import SessionLocal
        db = SessionLocal()

    query_filter = (room_or_device or "all").lower().strip()
    
    rows = db.execute(text("""
        SELECT s.id, s.name, s.type, s.device_id, s.min_threshold, s.max_threshold, r.name as room_name, s.tapo_status
        FROM monitoring.sensors s
        LEFT JOIN monitoring.rooms r ON s.room_id = r.id
        WHERE s.active = true
        ORDER BY r.name, s.name
    """)).fetchall()

    results = []
    for s in rows:
        s_id, s_name, s_type, dev_id, min_t, max_t, room_name, tapo_st = s
        r_name = room_name or s_name

        if query_filter != "all" and query_filter not in r_name.lower() and query_filter not in s_name.lower():
            continue

        item = {
            "sensor_name": s_name,
            "room_name": r_name,
            "type": s_type,
            "device_id": dev_id
        }

        if s_type == "plug":
            tel = db.execute(text("""
                SELECT apower, voltage, current, timestamp 
                FROM monitoring.plug_telemetry 
                WHERE device_id = :did 
                ORDER BY timestamp DESC LIMIT 1
            """), {"did": dev_id}).fetchone()
            if tel:
                item["power_watts"] = float(tel[0]) if tel[0] is not None else 0.0
                item["voltage_v"] = float(tel[1]) if tel[1] is not None else 230.0
                item["timestamp"] = tel[3].isoformat() if tel[3] else None
        elif s_type in ("temperature", "humidity"):
            tel = db.execute(text("""
                SELECT temperature, humidity, battery_level, timestamp 
                FROM monitoring.device_telemetry 
                WHERE device_id = :did 
                ORDER BY timestamp DESC LIMIT 1
            """), {"did": dev_id}).fetchone()
            if tel:
                item["temperature_c"] = float(tel[0]) if tel[0] is not None else None
                item["humidity_rh"] = float(tel[1]) if tel[1] is not None else None
                item["battery_pct"] = float(tel[2]) if tel[2] is not None else None
                item["timestamp"] = tel[3].isoformat() if tel[3] else None

        results.append(item)

    return {"total_sensors": len(results), "telemetry": results}


@register_tool(
    name="iot_set_maintenance",
    description="Starts or ends a maintenance window for a room, temporarily pausing temperature alerts.",
    parameters_schema={
        "type": "object",
        "properties": {
            "room_name": {"type": "string", "description": "Name of the room (e.g. 'Miso room', 'Vinegar room')"},
            "is_starting": {"type": "boolean", "description": "True to start maintenance (pause alerts), False to end maintenance (resume alerts)"},
            "reporter_name": {"type": "string", "description": "Name of the staff member performing maintenance"}
        },
        "required": ["room_name", "is_starting"]
    }
)
def iot_set_maintenance(room_name: str, is_starting: bool, reporter_name: str = "Staff", db: Optional[Session] = None) -> Dict[str, Any]:
    if not db:
        from groundup_webhooks.database import SessionLocal
        db = SessionLocal()

    room_lower = room_name.lower().replace("room", "").strip()
    row = db.execute(text("SELECT id, name FROM monitoring.rooms WHERE LOWER(name) LIKE :rname LIMIT 1"), {"rname": f"%{room_lower}%"}).fetchone()
    
    if not row:
        return {"error": f"Room '{room_name}' not found in database."}

    room_id, full_name = row
    db.execute(text("UPDATE monitoring.rooms SET is_under_maintenance = :maint WHERE id = :rid"), {"maint": is_starting, "rid": room_id})
    db.commit()

    emit_event(f"monitoring.maintenance.{'started' if is_starting else 'ended'}", {
        "room_name": full_name, "reported_by": reporter_name, "is_starting": is_starting
    }, actor_name=reporter_name, db=db)

    return {
        "room": full_name,
        "is_under_maintenance": is_starting,
        "alerts_paused": is_starting,
        "updated_at": datetime.utcnow().isoformat()
    }


# ─── 2. Recipe & Knowledge Base Tools ─────────────────────────────────────────

@register_tool(
    name="recipe_get_formula",
    description="Retrieves official Ground Up fermentation recipes with percentage ratios, aging time, and temperatures.",
    parameters_schema={
        "type": "object",
        "properties": {
            "product_name": {"type": "string", "description": "Name of product (e.g. 'Red Miso', 'White Miso', 'Shio Koji', 'Vinegar', 'Ginger Beer')"}
        },
        "required": ["product_name"]
    }
)
def recipe_get_formula(product_name: str, db: Optional[Session] = None) -> Dict[str, Any]:
    docs = search_knowledge(product_name, top_k=1, category="RECIPE")
    if not docs:
        return {"error": f"No official recipe found for '{product_name}'."}
    d = docs[0]
    return {
        "product_name": d["product_name"],
        "title": d["title"],
        "content": d["content"],
        "metadata": d["metadata"],
        "citation": d["citation"]
    }


@register_tool(
    name="recipe_scale_batch",
    description="Scales ingredient quantities by weight for a target batch size in kilograms.",
    parameters_schema={
        "type": "object",
        "properties": {
            "product_name": {"type": "string", "description": "Name of product (e.g. 'Red Miso', 'White Miso', 'Shio Koji')"},
            "target_batch_kg": {"type": "number", "description": "Target batch weight in kilograms"}
        },
        "required": ["product_name", "target_batch_kg"]
    }
)
def recipe_scale_batch(product_name: str, target_batch_kg: float, db: Optional[Session] = None) -> str:
    return scale_recipe_formula(product_name, float(target_batch_kg))


@register_tool(
    name="kb_search_sop",
    description="Semantic search across Ground Up SOPs for hygiene, cleaning, mold remediation, FSSAI/HACCP.",
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Topic or question (e.g. 'how to clean jars', 'white mold on miso', 'fssai hygiene')"},
            "top_k": {"type": "integer", "description": "Number of results to return", "default": 2}
        },
        "required": ["query"]
    }
)
def kb_search_sop(query: str, top_k: int = 2, db: Optional[Session] = None) -> Dict[str, Any]:
    results = search_knowledge(query, top_k=top_k)
    return {"query": query, "results": results}


# ─── 3. Production & Batch Tools ──────────────────────────────────────────────

@register_tool(
    name="production_get_jar",
    description="Fetches current status, aging duration, recipe details, and quality check timeline for a fermentation jar.",
    parameters_schema={
        "type": "object",
        "properties": {
            "jar_number": {"type": "integer", "description": "Integer jar number (e.g. 1, 42)"}
        },
        "required": ["jar_number"]
    }
)
def production_get_jar(jar_number: int, db: Optional[Session] = None) -> Dict[str, Any]:
    if not db:
        from groundup_webhooks.database import SessionLocal
        db = SessionLocal()

    jar_row = db.execute(text("SELECT id, jar_number, status, created_at FROM production.jars WHERE jar_number = :jnum"), {"jnum": jar_number}).fetchone()
    if not jar_row:
        return {"error": f"Jar #{jar_number} not found in database."}

    timeline = db.execute(text("SELECT action, details, created_at FROM production.jar_timeline WHERE jar_id = :jid ORDER BY created_at DESC"), {"jid": str(jar_row[0])}).fetchall()
    
    events = []
    for t in timeline:
        details = t[1] if isinstance(t[1], dict) else json.loads(t[1] or "{}")
        events.append({
            "action": t[0],
            "details": details,
            "created_at": t[2].isoformat() if t[2] else None
        })

    return {
        "jar_number": jar_number,
        "status": jar_row[2],
        "total_timeline_events": len(events),
        "timeline": events,
        "url": f"https://gu-production.initiativesewafoundation.com/jar.html?jar={jar_number}"
    }


@register_tool(
    name="production_record_qc",
    description="Records quality inspection scores (taste, smell, color, umami) and observations for a fermentation jar.",
    parameters_schema={
        "type": "object",
        "properties": {
            "jar_number": {"type": "integer", "description": "Integer jar number"},
            "taste_score": {"type": "integer", "description": "Score 1-10 for taste"},
            "smell_score": {"type": "integer", "description": "Score 1-10 for smell/aroma"},
            "color_score": {"type": "integer", "description": "Score 1-10 for visual color"},
            "notes": {"type": "string", "description": "Observation notes (e.g. 'rich aroma, no mold')"},
            "inspector_name": {"type": "string", "description": "Name of inspector"}
        },
        "required": ["jar_number"]
    }
)
def production_record_qc(
    jar_number: int,
    taste_score: Optional[int] = None,
    smell_score: Optional[int] = None,
    color_score: Optional[int] = None,
    notes: str = "",
    inspector_name: str = "Staff",
    db: Optional[Session] = None
) -> Dict[str, Any]:
    if not db:
        from groundup_webhooks.database import SessionLocal
        db = SessionLocal()

    scores = {}
    if taste_score is not None: scores["taste"] = taste_score
    if smell_score is not None: scores["smell"] = smell_score
    if color_score is not None: scores["color"] = color_score

    details = {
        "scores": scores,
        "notes": notes,
        "recorded_by": inspector_name,
        "timestamp": datetime.utcnow().isoformat()
    }

    db.execute(text("""
        INSERT INTO production.jar_timeline (id, jar_id, action, details, recorded_by, created_at)
        SELECT :eid, j.id, 'quality_check', CAST(:details AS jsonb), NULL, NOW()
        FROM production.jars j WHERE j.jar_number = :jnum
    """), {
        "eid": str(uuid.uuid4()), "jnum": jar_number, "details": json.dumps(details)
    })
    db.commit()

    return {
        "jar_number": jar_number,
        "action": "quality_check",
        "scores": scores,
        "recorded_by": inspector_name,
        "success": True
    }


@register_tool(
    name="production_package_batch",
    description="Packages a new batch of recipe ferment into an empty jar and sets aging duration.",
    parameters_schema={
        "type": "object",
        "properties": {
            "jar_number": {"type": "integer", "description": "Jar number to package"},
            "recipe_name": {"type": "string", "description": "Recipe name (e.g. 'Red Miso', 'White Miso')"},
            "batch_size_kg": {"type": "number", "description": "Batch weight in kilograms"},
            "container_type": {"type": "string", "description": "Plastic (50L) or Glass (20L)"},
            "packaged_by": {"type": "string", "description": "Staff member packaging batch"}
        },
        "required": ["jar_number", "recipe_name", "batch_size_kg"]
    }
)
def production_package_batch(
    jar_number: int,
    recipe_name: str,
    batch_size_kg: float,
    container_type: str = "Plastic (50L)",
    packaged_by: str = "Staff",
    db: Optional[Session] = None
) -> Dict[str, Any]:
    if not db:
        from groundup_webhooks.database import SessionLocal
        db = SessionLocal()

    now_dt = datetime.utcnow()
    ready_dt = now_dt + timedelta(days=90)

    details = {
        "batch_size": batch_size_kg,
        "jar_type": container_type,
        "notes": f"Recipe: {recipe_name}. Container: {container_type}. Packaged by {packaged_by}.",
        "recorded_by": packaged_by,
        "timestamp": now_dt.isoformat(),
        "target_ready_date": ready_dt.isoformat(),
        "fermentation_days": 90
    }

    db.execute(text("""
        INSERT INTO production.jar_timeline (id, jar_id, action, details, recorded_by, created_at)
        SELECT :eid, j.id, 'packaged', CAST(:details AS jsonb), NULL, NOW()
        FROM production.jars j WHERE j.jar_number = :jnum
    """), {
        "eid": str(uuid.uuid4()), "jnum": jar_number, "details": json.dumps(details)
    })
    db.commit()

    return {
        "jar_number": jar_number,
        "recipe": recipe_name,
        "batch_size_kg": batch_size_kg,
        "target_ready_date": ready_dt.strftime("%Y-%m-%d"),
        "success": True
    }


# ─── 4. Task & Operation Tools ────────────────────────────────────────────────

@register_tool(
    name="tasks_create_flipboard_task",
    description="Assigns a task to an employee on today's FlipBoard and dispatches WhatsApp notification card.",
    parameters_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Task description (e.g. 'Clean Miso Room fridge')"},
            "assigned_to": {"type": "string", "description": "Employee name (e.g. 'Ravi', 'Yadav', 'Gaya')"},
            "created_by": {"type": "string", "description": "Name of assigning person"}
        },
        "required": ["title", "assigned_to"]
    }
)
def tasks_create_flipboard_task(title: str, assigned_to: str, created_by: str = "System", db: Optional[Session] = None) -> Dict[str, Any]:
    if not db:
        from groundup_webhooks.database import SessionLocal
        db = SessionLocal()

    from groundup_webhooks.whatsapp_webhook import _sync_task_to_flipboard
    _sync_task_to_flipboard(title, assigned_to, db)

    emit_event("tasks.task.created", {
        "title": title, "created_by": created_by, "assigned_to_name": assigned_to
    }, actor_name=created_by, db=db)

    return {
        "title": title,
        "assigned_to": assigned_to,
        "created_by": created_by,
        "synced_to_flipboard": True
    }


@register_tool(
    name="tasks_mark_complete",
    description="Marks a task done on FlipBoard by task text or keyword match.",
    parameters_schema={
        "type": "object",
        "properties": {
            "task_hint": {"type": "string", "description": "Task keyword or number"},
            "completed_by": {"type": "string", "description": "Employee name marking task done"}
        },
        "required": ["task_hint"]
    }
)
def tasks_mark_complete(task_hint: str, completed_by: str = "Staff", db: Optional[Session] = None) -> Dict[str, Any]:
    if not db:
        from groundup_webhooks.database import SessionLocal
        db = SessionLocal()

    from groundup_webhooks.whatsapp_webhook import _get_today_daily_folder
    folder_id, _ = _get_today_daily_folder(db)

    row = db.execute(text("""
        SELECT i.id, i.text, i.assigned_to_name, i.page_id
        FROM flipboard.items i
        JOIN flipboard.pages p ON i.page_id = p.id
        WHERE p.folder_id = :fid AND i.status = 'active' AND LOWER(i.text) LIKE :hint
        LIMIT 1
    """), {"fid": folder_id, "hint": f"%{task_hint.lower()}%"}).fetchone()

    if not row:
        return {"error": f"Active task matching '{task_hint}' not found today."}

    item_id, task_text, assignee, page_id = row
    now_dt = datetime.utcnow()
    hide_dt = now_dt + timedelta(minutes=30)

    db.execute(text("""
        UPDATE flipboard.items
        SET status = 'done', completed_at = :cat, hide_after = :hat
        WHERE id = :tid
    """), {"tid": str(item_id), "cat": now_dt, "hat": hide_dt})
    db.commit()

    emit_event("tasks.task.completed", {
        "title": task_text, "completed_by": completed_by, "assigned_to_name": assignee
    }, actor_name=completed_by, db=db)

    return {
        "task_id": str(item_id),
        "task_text": task_text,
        "status": "done",
        "completed_by": completed_by,
        "success": True
    }


# ─── 5. Voice & Speech Synthesis Tool ─────────────────────────────────────────

@register_tool(
    name="voice_synthesize_audio",
    description="Synthesizes speech audio from text (Hindi, English, Marathi) and returns public media URL.",
    parameters_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to synthesize into speech"},
            "language": {"type": "string", "description": "Language code: 'hi', 'en', 'mr', 'bn'"}
        },
        "required": ["text"]
    }
)
def voice_synthesize_audio(text: str, language: str = "hi", db: Optional[Session] = None) -> Dict[str, Any]:
    res = synthesize_speech(text, language=language)
    if not res:
        return {"error": "Could not synthesize audio."}
    local_path, public_url = res
    return {
        "language": language,
        "audio_url": public_url,
        "success": True
    }


# ─── 6. Equipment Health & Compressor Analytics ───────────────────────────────

@register_tool(
    name="compressor_audit_health",
    description="Audits compressor duty cycles, short-cycling, and health degradation across all factory fridges/freezers.",
    parameters_schema={
        "type": "object",
        "properties": {
            "lookback_hours": {"type": "number", "description": "Lookback window in hours (default: 6.0)", "default": 6.0}
        },
        "required": []
    }
)
def compressor_audit_health(lookback_hours: float = 6.0, db: Optional[Session] = None) -> Dict[str, Any]:
    if not db:
        from groundup_webhooks.database import SessionLocal
        db = SessionLocal()
    from backend.services.compressor_analytics import audit_compressors_health
    reports = audit_compressors_health(db, lookback_hours=lookback_hours)
    return {"lookback_hours": lookback_hours, "total_units": len(reports), "reports": reports}


