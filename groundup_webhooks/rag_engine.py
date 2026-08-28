import json
import logging
import math
import re
from typing import List, Dict, Any, Optional
import httpx
from sqlalchemy import text

from groundup_webhooks.database import SessionLocal
from groundup_webhooks.config import GEMINI_API_KEY

logger = logging.getLogger("groundup_webhooks.rag_engine")

EMBEDDING_MODEL = "models/gemini-embedding-2"
GEMINI_GEN_MODEL = "models/gemini-2.5-flash"


def get_query_embedding(query_text: str) -> List[float]:
    """Generate vector embedding for a search query using Gemini Embedding 2."""
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY is not set.")
        return []
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/{EMBEDDING_MODEL}:embedContent?key={GEMINI_API_KEY}"
        payload = {
            "model": EMBEDDING_MODEL,
            "content": {
                "parts": [{"text": query_text}]
            }
        }
        resp = httpx.post(url, json=payload, timeout=12)
        if resp.status_code == 200:
            return resp.json().get("embedding", {}).get("values", [])
        else:
            logger.error(f"Query embedding error: {resp.status_code} - {resp.text}")
            return []
    except Exception as e:
        logger.error(f"Query embedding exception: {e}")
        return []


def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """Compute cosine similarity between two float vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot_product / (norm_a * norm_b)


def search_knowledge(
    query: str,
    top_k: int = 3,
    category: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Hybrid semantic + keyword search over Ground Up knowledge documents.
    Returns top_k items with similarity scores, citations, and metadata.
    """
    query_vec = get_query_embedding(query)
    db = SessionLocal()
    results = []
    
    try:
        sql = "SELECT id, category, product_name, title, content, embedding, metadata FROM production.knowledge_documents"
        params = {}
        if category:
            sql += " WHERE category = :cat"
            params["cat"] = category
            
        rows = db.execute(text(sql), params).fetchall()
        
        query_lower = query.lower()
        
        for row in rows:
            doc_id, cat, prod_name, title, content, emb_json, meta_json = row
            
            # 1. Semantic Similarity
            sim_score = 0.0
            if query_vec and emb_json:
                doc_vec = json.loads(emb_json) if isinstance(emb_json, str) else emb_json
                sim_score = cosine_similarity(query_vec, doc_vec)
                
            # 2. Keyword boost (e.g. exact product match)
            keyword_score = 0.0
            if prod_name and prod_name.lower() in query_lower:
                keyword_score += 0.25
            if title and any(w in query_lower for w in title.lower().split() if len(w) > 3):
                keyword_score += 0.15
                
            final_score = (sim_score * 0.7) + (keyword_score * 0.3)
            
            meta = json.loads(meta_json) if isinstance(meta_json, str) else (meta_json or {})
            
            results.append({
                "id": str(doc_id),
                "category": cat,
                "product_name": prod_name,
                "title": title,
                "content": content,
                "score": round(final_score, 4),
                "semantic_score": round(sim_score, 4),
                "metadata": meta,
                "citation": f"[Ground Up Knowledge Base: {title}]"
            })
            
        # Sort by final score descending
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]
        
    except Exception as e:
        logger.error(f"Search knowledge error: {e}")
        return []
    finally:
        db.close()


def answer_with_rag(
    user_query: str,
    sender_name: str = "Team Member",
    preferred_lang: str = "en"
) -> str:
    """
    RAG Answer Generator:
    1. Retrieves top matching knowledge chunks.
    2. Constructs grounded prompt for Gemini with citations.
    3. Returns formatted response in requested language.
    """
    top_docs = search_knowledge(user_query, top_k=2)
    
    if not top_docs or top_docs[0]["score"] < 0.35:
        # Fallback to direct Gemini generation if no relevant knowledge found
        context_str = "No specific Ground Up internal SOP found. Answer using standard artisan fermentation best practices."
        citation = ""
    else:
        context_str = "\n\n---\n\n".join([
            f"DOCUMENT: {d['title']} ({d['category']})\n{d['content']}" for d in top_docs
        ])
        citation = f"\n\n📚 *Source:* {top_docs[0]['title']}"

    system_prompt = f"""You are 'Chef Gaya', the master fermentation expert and AI companion for Ground Up Fermentary.
You provide precise, factory-standard advice for recipes, ingredient ratios, temperatures, times, and sanitation.

GROUND UP VERIFIED KNOWLEDGE:
{context_str}

USER DETAILS:
Name: {sender_name}
Query: {user_query}
Preferred Language: {preferred_lang} (en=English, hi=Hindi, mr=Marathi, bn=Bengali)

INSTRUCTIONS:
1. Always cite exact numbers (temperatures, salt %, aging days/months, room names).
2. Answer in a warm, concise, professional chef tone.
3. If language is Hindi/Hinglish, explain clearly in conversational Roman Hindi or Devanagari.
4. Format using clean WhatsApp-friendly bullet points and emojis.
5. End with the official citation if available."""

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/{GEMINI_GEN_MODEL}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": system_prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 600}
        }
        resp = httpx.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            ans = resp.json().get("candidates", [])[0].get("content", {}).get("parts", [])[0].get("text", "")
            return ans.strip()
        else:
            logger.error(f"Gemini generation error: {resp.status_code} - {resp.text}")
    except Exception as e:
        logger.error(f"RAG generation exception: {e}")

    # Fallback if Gemini generation fails
    if top_docs:
        d = top_docs[0]
        return f"🧑‍🍳 *Chef Gaya (Ground Up Knowledge)*\n\n📌 *{d['title']}*\n\n{d['content'][:400]}...{citation}"
    return "Chef Gaya is checking the fermentation logs. Please ask again in a moment."


def scale_recipe_formula(product_query: str, target_batch_kg: float) -> str:
    """
    Computes exact ingredient weights scaled to target_batch_kg.
    """
    docs = search_knowledge(product_query, top_k=1, category="RECIPE")
    if not docs:
        return f"Could not find an official recipe formula matching '{product_query}'."
    
    doc = docs[0]
    meta = doc.get("metadata", {})
    ratios = meta.get("ratios", {})
    
    if not ratios:
        return f"Recipe '{doc['title']}' does not have structured percentage ratios configured."
        
    lines = [
        f"⚖️ *Scaled Recipe Formula: {doc['product_name']}*",
        f"🎯 *Target Batch Size:* {target_batch_kg:.2f} kg",
        f"📍 *Fermentation Room:* {meta.get('room', 'Miso Room')}",
        "",
        "*Required Ingredients:*"
    ]
    
    for ing, pct in ratios.items():
        ing_clean = ing.replace("_pct", "").replace("_", " ").title()
        weight_kg = (pct / 100.0) * target_batch_kg
        weight_g = weight_kg * 1000.0
        if weight_kg >= 1.0:
            lines.append(f"  • *{ing_clean}:* {weight_kg:.2f} kg ({pct}%)")
        else:
            lines.append(f"  • *{ing_clean}:* {weight_g:.0f} g ({pct}%)")
            
    if "temp_c" in meta:
        lines.append(f"\n🌡️ *Temperature:* {meta['temp_c']}°C")
    if "aging_months" in meta:
        lines.append(f"⏳ *Aging Time:* {meta['aging_months']} months")
    if "ph_range" in meta:
        lines.append(f"🧪 *Target pH:* {meta['ph_range']}")
        
    lines.append(f"\n📚 *Reference:* {doc['title']}")
    return "\n".join(lines)
