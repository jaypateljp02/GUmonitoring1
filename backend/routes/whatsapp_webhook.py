import os
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.models.maintenance_event import MaintenanceEvent
from backend.models.room import Room
import httpx

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook/whatsapp", tags=["WhatsApp Webhook"])

@router.get("")
async def verify_webhook(request: Request):
    """Verify webhook for Meta WhatsApp API."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    # Use a configured token or default
    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "gu_monitoring_secret_token")

    if mode and token:
        if mode == "subscribe" and token == verify_token:
            logger.info("WhatsApp webhook verified successfully.")
            return Response(content=challenge, media_type="text/plain")
        else:
            return Response(status_code=403)
    return Response(status_code=400)

@router.post("")
async def handle_whatsapp_message(request: Request, db: Session = Depends(get_db)):
    """Handle incoming WhatsApp messages (e.g. maintenance status)."""
    try:
        body = await request.json()
    except:
        return Response(status_code=400)
        
    try:
        # Parse Meta's nested JSON structure
        entry = body.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])
        
        if not messages:
            return Response(status_code=200)
            
        message = messages[0]
        sender_phone = message.get("from")
        msg_type = message.get("type")
        
        if msg_type != "text":
            return Response(status_code=200)
            
        text_body = message.get("text", {}).get("body", "").strip()
        logger.info(f"Received WhatsApp message from {sender_phone}: {text_body}")
        
        # Simple AI parsing or Keyword parsing
        # We will use Gemini to extract intent
        await process_maintenance_message(text_body, sender_phone, db)
        
    except Exception as e:
        logger.error(f"Error processing WhatsApp webhook: {e}", exc_info=True)
        
    return Response(status_code=200)

async def process_maintenance_message(text: str, phone: str, db: Session):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.warning("No Gemini API key for webhook parsing.")
        return
        
    rooms = db.query(Room).filter(Room.active == True).all()
    room_names = [r.name for r in rooms]
    
    prompt = f"""
    You are an AI assistant parsing an incoming WhatsApp message from a factory employee.
    The message might be about starting or ending a cleaning/maintenance task for a fridge/room.
    Available rooms: {', '.join(room_names)}.
    Message: "{text}"
    
    Extract the following information:
    1. is_maintenance_update: true if this message is about starting or ending maintenance/cleaning, else false.
    2. action: "start" or "end".
    3. room_name: The exact name of the room from the available list that best matches the message.
    4. event_type: e.g., "cleaning", "defrosting", "maintenance".
    
    Return ONLY a JSON object:
    {{
        "is_maintenance_update": true/false,
        "action": "start" / "end",
        "room_name": "...",
        "event_type": "..."
    }}
    """
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1}
    }
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=10.0)
            data = resp.json()
            response_text = data["candidates"][0]["content"]["parts"][0]["text"]
            
            # Clean json
            import json
            import re
            match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                
                if parsed.get("is_maintenance_update") and parsed.get("room_name"):
                    room = db.query(Room).filter(Room.name == parsed["room_name"]).first()
                    if not room:
                        logger.warning(f"Could not find room {parsed['room_name']}")
                        return
                        
                    action = parsed.get("action")
                    event_type = parsed.get("event_type", "cleaning")
                    now_utc = datetime.utcnow()
                    
                    if action == "start":
                        # Check if already active
                        active = db.query(MaintenanceEvent).filter(
                            MaintenanceEvent.room_id == room.id,
                            MaintenanceEvent.ended_at == None
                        ).first()
                        if not active:
                            new_event = MaintenanceEvent(
                                room_id=room.id,
                                event_type=event_type,
                                started_at=now_utc,
                                reported_by_phone=phone,
                                notes=text
                            )
                            db.add(new_event)
                            db.commit()
                            send_whatsapp_reply(phone, f"✅ Started {event_type} for {room.name}. Alerts suppressed.")
                    elif action == "end":
                        active = db.query(MaintenanceEvent).filter(
                            MaintenanceEvent.room_id == room.id,
                            MaintenanceEvent.ended_at == None
                        ).first()
                        if active:
                            active.ended_at = now_utc
                            db.commit()
                            send_whatsapp_reply(phone, f"✅ Ended {event_type} for {room.name}. Alerts enabled.")
    except Exception as e:
        logger.error(f"Error parsing maintenance message with Gemini: {e}")

def send_whatsapp_reply(to_phone: str, message: str):
    """Send a plain text reply to the user (only works within 24h of their message)."""
    access_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    
    if not access_token or not phone_number_id:
        return
        
    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": message
        }
    }
    
    # We can fire and forget synchronously since this is just a quick reply
    try:
        import requests
        requests.post(url, headers=headers, json=payload, timeout=5)
    except Exception as e:
        logger.error(f"Failed to send whatsapp reply: {e}")
