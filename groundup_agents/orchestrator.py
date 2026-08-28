import json
import logging
import re
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from .chef_gaya import ChefGayaAgent
from .production import ProductionAgent
from .monitoring import MonitoringAgent
from groundup_webhooks.models import WhatsAppMessage

logger = logging.getLogger("groundup_agents.orchestrator")


class AgentOrchestrator:
    """
    Ground Up & Sugam Central Agent Orchestrator.
    Classifies intent and routes incoming requests to the specialized agent or task workflow.
    """

    def __init__(self):
        self.chef_gaya = ChefGayaAgent()
        self.production = ProductionAgent()
        self.monitoring = MonitoringAgent()

    async def dispatch(
        self,
        raw_text: str,
        sender_phone: str,
        sender_name: str,
        parsed_intent: Dict[str, Any],
        db: Session
    ) -> str:
        """Main routing pipeline."""
        lower = raw_text.lower().strip()
        intent = parsed_intent.get("intent", "general_chat")

        logger.info(f"🤖 [Orchestrator] Routing: sender={sender_name} ({sender_phone}) | intent={intent} | text='{raw_text[:60]}'")

        # ── 1. ROUTING: Chef Gaya (Recipes, Fermentation, Mold, Sanitation RAG) ──
        is_chef_query = (
            intent == "recipe_query"
            or any(kw in lower for kw in [
                "recipe", "ingredients", "banane ka tarika", "kitna daalna",
                "scale", "koji ratio", "salt percentage", "kahm yeast",
                "mold", "shio koji", "miso recipe", "vinegar recipe",
                "ginger beer recipe", "sanitation sop", "sterilization"
            ])
            and not ("jar" in lower and any(w in lower for w in ["taste", "smell", "color", "score"]))
        )

        if is_chef_query:
            return await self.chef_gaya.handle(raw_text, sender_phone, sender_name, parsed_intent, db)

        # ── 2. ROUTING: Production & Batching (Jars, QC, Packaging, Supply) ────
        is_production_query = (
            intent in ("jar_check", "supply_request", "production_log")
            or "jar" in lower
            or any(kw in lower for kw in ["khatam", "order karo", "supply", "batch", "packed", "packaging", "taste", "smell", "score"])
        )

        if is_production_query:
            return await self.production.handle(raw_text, sender_phone, sender_name, parsed_intent, db)

        # ── 3. ROUTING: IoT Floor & Telemetry (Temperature, Power, Maintenance) ─
        is_monitoring_query = (
            intent in ("maintenance_update", "issue_report", "sensor_query")
            or any(kw in lower for kw in [
                "temperature", "temp history", "sensor log", "power", "watt", "voltage",
                "cleaning fridge", "cleaning room", "maintenance", "leak", "broken",
                "is miso room cold", "fridge temp", "telemetry"
            ])
        )

        if is_monitoring_query:
            return await self.monitoring.handle(raw_text, sender_phone, sender_name, parsed_intent, db)

        # ── 4. ROUTING: Fallback to Chef Gaya Persona for General Inquiries ───
        return await self.chef_gaya.handle(raw_text, sender_phone, sender_name, parsed_intent, db)


# Singleton instance
orchestrator = AgentOrchestrator()
