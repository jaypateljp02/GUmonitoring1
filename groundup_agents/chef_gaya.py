import re
import logging
from typing import Dict, Any
from sqlalchemy.orm import Session

from .base import BaseAgent
from groundup_webhooks.rag_engine import search_knowledge, scale_recipe_formula

logger = logging.getLogger("groundup_agents.chef_gaya")


class ChefGayaAgent(BaseAgent):
    """
    Chef Gaya Agent:
    Master Fermentation Specialist & Culinary AI for Ground Up Fermentary.
    Answers recipe ratios, fermentation temperatures, batch scaling, and mold/SOP troubleshooting.
    """

    def __init__(self):
        super().__init__(
            name="Chef Gaya",
            role_description="Master Fermenter & Head of Fermentation Operations",
            system_instructions="""You are 'Chef Gaya', the master fermentation expert for Ground Up Fermentary.
You have deep expertise in traditional Japanese koji propagation, artisanal miso (Red, White, Sesame, Seaweed), raw vinegars, living shio koji, fermented beverages, and HACCP/FSSAI hygiene.

Core Principles:
1. Precision in numbers: always cite exact percentages (e.g. 12% salt for Red Miso vs 7% for White Miso), room temperatures (22-28°C for Miso Room), and aging durations.
2. Safety first: White Kahm yeast is harmless surface yeast (scrape 1cm + wipe alcohol + re-salt). Green/black mold is hazardous (isolate/quarantine).
3. Warm, encouraging chef persona that communicates clearly in English, Hindi, and Hinglish."""
        )

    async def handle(
        self,
        query: str,
        sender_phone: str,
        sender_name: str,
        parsed_intent: Dict[str, Any],
        db: Session
    ) -> str:
        lower = query.lower().strip()

        # 1. Detect Recipe Scaling Request (e.g., "scale 50kg red miso", "make 30kg white miso", "batch 25kg koji")
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

        # 2. Query Vector RAG Knowledge Base
        top_docs = search_knowledge(query, top_k=2)
        if top_docs and top_docs[0]["score"] >= 0.35:
            context_str = "\n\n---\n\n".join([
                f"DOCUMENT: {d['title']} ({d['category']})\n{d['content']}" for d in top_docs
            ])
            citation = f"\n\n📚 *Reference:* {top_docs[0]['title']}"
        else:
            context_str = "No specific factory document found. Provide advice based on standard Ground Up artisanal koji/fermentation standards."
            citation = ""

        # Determine user language preference
        preferred_lang = parsed_intent.get("language", "en")
        if any(w in lower for w in ["kya", "kaise", "kitna", "batao", "karo", "hai", "khatam"]):
            preferred_lang = "hi"

        response = await self.generate_response(
            user_query=query,
            context_data=context_str,
            sender_name=sender_name,
            phone=sender_phone,
            preferred_lang=preferred_lang,
            db=db,
            temperature=0.2
        )

        if citation and "Reference:" not in response and "Source:" not in response:
            response += citation

        return response
