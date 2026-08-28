import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import httpx
from sqlalchemy.orm import Session
from sqlalchemy import text

from groundup_webhooks.config import GEMINI_API_KEY

logger = logging.getLogger("groundup_agents.base")

DEFAULT_MODEL = "models/gemini-2.5-flash"


class BaseAgent(ABC):
    """
    Abstract Foundation for all Ground Up / Sugam Specialist Agents.
    Handles conversation state, episodic memory lookup, tool execution, and multi-lingual output.
    """

    def __init__(self, name: str, role_description: str, system_instructions: str):
        self.name = name
        self.role_description = role_description
        self.system_instructions = system_instructions

    def get_recent_history(self, phone: str, db: Session, limit: int = 4) -> List[Dict[str, str]]:
        """Retrieve recent conversation history for this sender."""
        if not phone or not db:
            return []
        try:
            rows = db.execute(text("""
                SELECT direction, raw_content 
                FROM webhooks.whatsapp_messages 
                WHERE phone_number = :ph AND message_type IN ('text', 'audio')
                ORDER BY created_at DESC 
                LIMIT :lim
            """), {"ph": phone, "lim": limit}).fetchall()
            
            history = []
            for r in reversed(rows):
                role = "user" if r[0] == "incoming" else "model"
                history.append({"role": role, "parts": [{"text": r[1]}]})
            return history
        except Exception as e:
            logger.warning(f"Error fetching history for {phone}: {e}")
            return []

    def get_episodic_memories(self, subject: str, db: Session, limit: int = 3) -> str:
        """Fetch recent episodic memory events related to the topic."""
        if not db:
            return ""
        try:
            rows = db.execute(text("""
                SELECT summary, occurred_at 
                FROM webhooks.memory_episodic
                WHERE subject ILIKE :subj OR summary ILIKE :subj
                ORDER BY occurred_at DESC
                LIMIT :lim
            """), {"subj": f"%{subject}%", "lim": limit}).fetchall()
            if rows:
                return "\n".join([f"- [{r[1].strftime('%d %b %H:%M')}] {r[0]}" for r in rows])
        except Exception as e:
            logger.warning(f"Error fetching episodic memory: {e}")
        return ""

    async def generate_response(
        self,
        user_query: str,
        context_data: str,
        sender_name: str = "Staff",
        phone: str = "",
        preferred_lang: str = "en",
        db: Optional[Session] = None,
        temperature: float = 0.2
    ) -> str:
        """Call Gemini to generate a structured, persona-grounded response."""
        if not GEMINI_API_KEY:
            return "Agent system configuration error: GEMINI_API_KEY missing."

        history = self.get_recent_history(phone, db, limit=4) if (db and phone) else []

        full_system_prompt = f"""You are '{self.name}' ({self.role_description}) at Ground Up Fermentary in Pune, India.
{self.system_instructions}

CONTEXT & KNOWLEDGE DATA:
{context_data}

USER DETAILS:
Name: {sender_name}
Preferred Language: {preferred_lang} (en=English, hi=Hindi/Hinglish, mr=Marathi, bn=Bengali)

OUTPUT GUIDELINES:
1. Provide accurate, factory-ready answers with exact numbers and clear steps.
2. If Hindi/Hinglish is preferred, reply in conversational Roman Hindi or Devanagari.
3. Use clean WhatsApp formatting (bold headings, bullet points, concise emojis).
4. Do not make up facts outside the provided Ground Up verified context.
5. End with the official reference when available."""

        payload_contents = []
        # Add history if available
        for h in history:
            payload_contents.append(h)
            
        # Add current turn
        payload_contents.append({
            "role": "user",
            "parts": [{"text": f"{full_system_prompt}\n\nUser Query: {user_query}"}]
        })

        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/{DEFAULT_MODEL}:generateContent?key={GEMINI_API_KEY}"
            payload = {
                "contents": payload_contents,
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": 650
                }
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    ans = resp.json().get("candidates", [])[0].get("content", {}).get("parts", [])[0].get("text", "")
                    return ans.strip()
                else:
                    logger.error(f"Gemini API error in {self.name}: {resp.status_code} - {resp.text}")
        except Exception as e:
            logger.error(f"Exception in {self.name} generation: {e}")

        return f"[{self.name}] Could not process your request at this moment. Please retry."

    @abstractmethod
    async def handle(
        self,
        query: str,
        sender_phone: str,
        sender_name: str,
        parsed_intent: Dict[str, Any],
        db: Session
    ) -> str:
        """Core execution logic implemented by each specialist agent."""
        pass
