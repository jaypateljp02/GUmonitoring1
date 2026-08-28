import json
import logging
import os
import sys
import httpx
from sqlalchemy import text
from groundup_webhooks.database import SessionLocal, engine
from groundup_webhooks.config import GEMINI_API_KEY

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("groundup_knowledge_seeder")

EMBEDDING_MODEL = "models/gemini-embedding-2"

def get_embedding(text_content: str) -> list:
    """Generate 3072-dim vector embedding using Gemini Embedding 2."""
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not set.")
    url = f"https://generativelanguage.googleapis.com/v1beta/{EMBEDDING_MODEL}:embedContent?key={GEMINI_API_KEY}"
    payload = {
        "model": EMBEDDING_MODEL,
        "content": {
            "parts": [{"text": text_content}]
        }
    }
    resp = httpx.post(url, json=payload, timeout=20)
    if resp.status_code != 200:
        logger.error(f"Embedding error: {resp.status_code} - {resp.text}")
        raise RuntimeError(f"Embedding API failed: {resp.text}")
    return resp.json().get("embedding", {}).get("values", [])


KNOWLEDGE_ITEMS = [
    {
        "category": "RECIPE",
        "product_name": "Red Miso (Aka Miso)",
        "title": "Red Miso (Aka Miso) Master Recipe & Production Standard",
        "content": """[Ground Up Fermentary - Red Miso Master Recipe]
Product: Red Miso (Aka Miso)
Flavor Profile: Deep, robust, savory umami, rich dark reddish-brown color with long aging.

Official Ingredient Ratios (by weight):
- Cooked Soybeans: 50.0% (Soaked 18h, steamed 4-5h until soft enough to crush between thumb and pinky)
- Rice Koji (Aspergillus oryzae): 38.0% (Well-inoculated, sweet aroma, fragrant white mycelium)
- Sea Salt: 12.0% (Non-iodized, pure mineral sea salt)
- Reserved Bean Cooking Liquid: 3-5% as needed for paste consistency (target moisture: 46-48%)

Fermentation Specifications:
- Fermentation Room: Miso Room (Target: 22°C - 28°C, 75-85% RH)
- Aging Duration: Minimum 6 months, optimal 9-12 months
- Target pH: 4.8 - 5.2
- Weighting: Place food-grade plastic sheet + salt ring on surface, apply weight equal to 1.5x batch weight to prevent mold and express tamari.
- Quality Checkpoints: After Month 1 (first aroma shift), Month 3 (darkening & umami development), Month 6 (harvest testing).""",
        "metadata": {
            "ratios": {"soybeans_pct": 50, "koji_pct": 38, "salt_pct": 12},
            "temp_c": "22-28",
            "aging_months": "6-12",
            "room": "Miso Room",
            "ph_range": "4.8-5.2"
        }
    },
    {
        "category": "RECIPE",
        "product_name": "White Miso (Shiro Miso)",
        "title": "White Miso (Shiro Miso) Master Recipe & Production Standard",
        "content": """[Ground Up Fermentary - White Miso Master Recipe]
Product: White Miso (Shiro Miso)
Flavor Profile: Sweet, delicate, mild umami, pale golden-yellow color with short fermentation.

Official Ingredient Ratios (by weight):
- Cooked Soybeans: 45.0% (Boiled with water change to keep color light)
- Rice Koji: 48.0% (High koji ratio provides natural sweetness and rapid enzyme activity)
- Sea Salt: 7.0% (Lower salt content for sweeter profile)
- Filtered Water / Cooking Liquid: 3-5% (Target moisture: 50-52%)

Fermentation Specifications:
- Fermentation Room: Miso Room (Target: 24°C - 28°C, 75-85% RH)
- Aging Duration: 3 weeks to 8 weeks (quick cycle)
- Target pH: 5.0 - 5.4
- Weighting: Light weight press (0.5x batch weight)
- Storage After Harvest: Must be moved to Cold Storage (0.5°C - 4.0°C) immediately after packing to stop fermentation and preserve light color.""",
        "metadata": {
            "ratios": {"soybeans_pct": 45, "koji_pct": 48, "salt_pct": 7},
            "temp_c": "24-28",
            "aging_weeks": "3-8",
            "room": "Miso Room",
            "ph_range": "5.0-5.4"
        }
    },
    {
        "category": "RECIPE",
        "product_name": "Toasted Sesame Miso",
        "title": "Toasted Sesame Miso Specialty Recipe",
        "content": """[Ground Up Fermentary - Toasted Sesame Miso]
Product: Toasted Sesame Miso
Flavor Profile: Nutty, aromatic, complex savory paste with rich roasted sesame notes.

Official Ingredient Ratios (by weight):
- Cooked Soybeans: 45.0%
- Rice Koji: 35.0%
- Freshly Toasted White & Black Sesame Paste: 10.0% (Slowly roasted at 150°C and ground to smooth paste)
- Sea Salt: 10.0%
- Fermentation Room: Miso Room (22°C - 26°C)
- Aging Duration: 3 to 6 months
- Quality Standard: Homogeneous brown paste, high sesame fragrance, no rancidity.""",
        "metadata": {
            "ratios": {"soybeans_pct": 45, "koji_pct": 35, "sesame_paste_pct": 10, "salt_pct": 10},
            "temp_c": "22-26",
            "aging_months": "3-6",
            "room": "Miso Room"
        }
    },
    {
        "category": "RECIPE",
        "product_name": "Seaweed Miso (Kombu & Wakame)",
        "title": "Seaweed Miso (Kombu & Wakame) Specialty Recipe",
        "content": """[Ground Up Fermentary - Seaweed Miso]
Product: Seaweed Miso (Kombu & Wakame)
Flavor Profile: Intense mineral ocean umami, natural glutamic acid boost from kombu.

Official Ingredient Ratios:
- Cooked Soybeans: 48.0%
- Rice Koji: 38.0%
- Dried Kombu & Wakame Flakes (Hydrated in hot water): 4.0%
- Sea Salt: 10.0%
- Fermentation Room: Wild Room / Miso Room (22°C - 26°C)
- Aging Duration: 6 to 9 months
- Ideal for miso soups, marinades, and dashi glazes.""",
        "metadata": {
            "ratios": {"soybeans_pct": 48, "koji_pct": 38, "seaweed_pct": 4, "salt_pct": 10},
            "room": "Wild room / Miso Room",
            "aging_months": "6-9"
        }
    },
    {
        "category": "RECIPE",
        "product_name": "Shio Koji (Umami Marinade)",
        "title": "Shio Koji Master Preparation & Usage Protocol",
        "content": """[Ground Up Fermentary - Shio Koji Master Recipe]
Product: Shio Koji (Living Rice Koji Marinade & Seasoning)
Flavor Profile: Sweet, salty, enzymatically active, natural meat tenderizer and vegetable cure.

Standard Batch Formula (1 kg batch):
- Fresh Rice Koji: 500g (50.0%)
- Filtered Water: 400g (40.0%)
- Sea Salt: 100g (10.0%)

Preparation Steps:
1. In a sanitized stainless steel bowl, rub rice koji grains between hands to separate kernels.
2. Add sea salt and blend thoroughly with dry koji.
3. Pour in filtered water (25°C-30°C) and stir until fully combined.
4. Transfer into sanitized glass/food-grade fermentation jars, loosely capped.
5. Fermentation: Room temperature (24°C - 28°C) for 7 to 10 days.
6. Daily Maintenance: Stir thoroughly once every 24 hours with a sanitized silicone spatula to aerate and redistribute enzymes.
7. Completion Criteria: Liquid turns milky cream with a sweet fermented aroma (like amazake), rice grains soften completely.
8. Post-Harvest: Store in Cold Fridge (0.5°C - 4.0°C). Shelf life: 6 months.""",
        "metadata": {
            "ratios": {"koji_pct": 50, "water_pct": 40, "salt_pct": 10},
            "fermentation_days": "7-10",
            "temp_c": "24-28",
            "shelf_life_fridge": "6 months"
        }
    },
    {
        "category": "RECIPE",
        "product_name": "Artisanal Rice & Fruit Vinegars",
        "title": "Artisanal Vinegar Two-Stage Fermentation SOP",
        "content": """[Ground Up Fermentary - Artisanal Vinegar SOP]
Product: Raw Unpasteurized Rice & Fruit Vinegars (Apple Cider, Pineapple, Jamun)

Two-Stage Fermentation Protocol:
Stage 1: Alcoholic Fermentation (Anaerobic / Air-locked)
- Raw Material: Fruit juice / Koji rice mash (Sugar adjusted to 14 - 18° Brix)
- Inoculant: Wine yeast (Saccharomyces cerevisiae)
- Target: 6.0% - 9.0% ABV (Alcohol by Volume)
- Duration: 10 - 14 days at 20°C - 24°C until specific gravity drops to 0.998 - 1.002.

Stage 2: Acetification Fermentation (Aerobic)
- Room: Vinegar Room (Target: 26°C - 32°C, 70-80% RH)
- Inoculant: 20% Active Unpasteurized Mother of Vinegar (Acetobacter aceti)
- Vessel: Food-grade barrels/glass with breathable cheesecloth/mesh cover for oxygen circulation.
- Target Acetic Acid: 5.0% - 6.5% total acidity (titratable acidity)
- Target pH: < 3.2
- Duration: 4 to 8 weeks depending on ambient temperature and aeration.
- Harvesting & Bottling: Siphon clear vinegar from beneath the mother pellicle, fine filter, bottle in amber glass.""",
        "metadata": {
            "stage_1": "Alcoholic (14-18 Brix -> 6-9% ABV)",
            "stage_2": "Acetification (26-32°C, 20% Mother)",
            "target_acidity": "5.0-6.5% acetic acid",
            "target_ph": "< 3.2",
            "room": "Vinegar room"
        }
    },
    {
        "category": "RECIPE",
        "product_name": "Naturally Fermented Ginger Beer & Probiotic Sodas",
        "title": "Fermented Ginger Beer & Probiotic Soda SOP",
        "content": """[Ground Up Fermentary - Fermented Ginger Beer & Probiotic Sodas]
Product: Naturally Carbonated Ginger Beer

1. Ginger Bug Culture Maintenance:
- Daily feed: 15g organic grated unpeeled ginger + 15g raw cane sugar + 30ml filtered water.
- Keep at 22°C - 26°C until active, fizzy, and bubbly (4-5 days).

2. Brew Formulation (per 10 Liters):
- Fresh Ginger Decoction: 800g grated ginger simmered in 3L water for 25 mins.
- Cane Sugar: 800g (Target initial Brix: 8.0 - 9.5°Bx).
- Fresh Lemon/Lime Juice: 250ml.
- Active Ginger Bug Starter: 500ml (5% inoculation).
- Filtered Water: Make up to 10 Liters total volume.

3. Bottling & Carbonation:
- Bottle in heavy-duty pressure-rated flip-top bottles or PET test bottles.
- Primary Carbonation: 24 to 48 hours at 24°C - 26°C until firm/carbonated.
- CRITICAL SAFETY STEP (Cold Crash): Once carbonation is achieved, immediately transfer ALL bottles to Terrace Fridge / Cold Storage (2°C - 4°C) to arrest yeast activity and prevent bottle over-pressurization.""",
        "metadata": {
            "starter": "Ginger Bug 5%",
            "brix": "8.0-9.5",
            "carbonation_hours": "24-48h at 24-26C",
            "cold_crash_temp": "2-4C"
        }
    },
    {
        "category": "SOP",
        "product_name": "Environmental Standards",
        "title": "Ground Up Factory Environmental & Temperature Standard Operating Procedure",
        "content": """[Ground Up Factory - Environmental Standard Operating Procedure]
Standard operating temperature, humidity, and alert tolerances for all factory rooms and refrigeration equipment:

1. Fermentation Rooms:
- Miso Room: 22.0°C to 28.0°C | 70% to 85% RH (Smart Plug: 10029128ba, Sensor: a4b002898f). Alert triggered if Temp > 30.0°C or < 18.0°C for > 30 mins.
- Vinegar Room: 26.0°C to 32.0°C | 65% to 80% RH (Smart Plug: 10029128c9, Sensor: a4b0028991). Alert triggered if Temp < 24.0°C or > 35.0°C.
- Wild Room: 20.0°C to 26.0°C | 60% to 75% RH (Sensor: a4b0028a85).

2. Cold Storage Refrigeration (Storage & Conditioning):
- Black Fridge: 0.5°C to 4.0°C (Sensor: a4b002884e, Plug: 1002912a22). Alert if > 4.0°C for > 15 mins.
- Hall White Fridge: 0.5°C to 4.0°C (Sensor: a4b00292f1, Plug: 10029128be). Alert if > 4.0°C for > 15 mins.
- Terrace Fridge: 0.5°C to 4.0°C (Sensor: a4b0028d62, Plug: 10029128cd).
- Terrace Fridge Steel: 0.5°C to 4.0°C (Sensor: a4b0028a88, Plug: 1002912934).
- Samsung Fridge: 0.5°C to 4.0°C (Sensor: a4b0028d6f, Plug: 100291168e).

3. Deep Freezers (Koji & Raw Material Preservation):
- Main Freezer: -22.0°C to -15.0°C (Sensor: a4b0028f2f, Plug: 10029128ab). Alert if > -10.0°C.
- Samsung Freezer: -22.0°C to -15.0°C (Sensor: a4b0028aa7). Alert if > -10.0°C.
- Hall Freezer: -22.0°C to -15.0°C (Sensor: a4b0028a87, Plug: 1002912491). Alert if > -10.0°C.""",
        "metadata": {
            "miso_room_range": "22-28C",
            "vinegar_room_range": "26-32C",
            "fridge_range": "0.5-4.0C",
            "freezer_range": "-22 to -15C"
        }
    },
    {
        "category": "SOP",
        "product_name": "Sanitation & Sterilization",
        "title": "Fermentation Equipment Cleaning & Sterilization SOP (HACCP & FSSAI)",
        "content": """[Ground Up Factory - Sanitation & Sterilization SOP]
Compliance: FSSAI Schedule 4 & HACCP Good Manufacturing Practices (GMP)

Daily Sanitization Protocol:
1. Three-Sink Washing Procedure for Fermentation Tools, Bowls, and Sifters:
   - Sink 1 (Wash): Hot water (45°C - 50°C) with food-grade unscented neutral detergent. Scrub away all organic residue.
   - Sink 2 (Rinse): Clean potable running water rinse to remove all detergent traces.
   - Sink 3 (Sanitize): Food-grade sanitizer solution (e.g. 100-200 ppm Chlorine dioxide or Peracetic Acid 0.2%) soaked for minimum 2 minutes.
   - Air Drying: Inverted on sanitized stainless steel drying racks in clean zone. Never use cloth towels to dry.

2. Fermentation Jar & Barrel Preparation:
   - Wash thoroughly with hot water and food-grade alkaline wash.
   - Spray inside and rim with 70% Food-Grade Isopropyl Alcohol (IPA) / Ethanol.
   - Allow alcohol to fully evaporate for 60 seconds before filling with koji mash.

3. Personal Hygiene Rules for Factory Staff:
   - Mandatory hairnets, beard nets, and clean apron before entering Miso or Vinegar room.
   - Handwashing: 20-second scrub with antibacterial soap under warm water before handling koji or jars.
   - No footwear from outside permitted; dedicated sanitized factory clogs must be worn.""",
        "metadata": {
            "standard": "FSSAI Schedule 4 / HACCP GMP",
            "sanitizer": "70% Food-Grade Alcohol / Peracetic Acid 200ppm",
            "sinks": "Wash (45-50C) -> Rinse -> Sanitize (2 mins)"
        }
    },
    {
        "category": "TROUBLESHOOTING",
        "product_name": "Fermentation Troubleshooting",
        "title": "Miso & Vinegar Mold Identification and Remediation Guide",
        "content": """[Ground Up Factory - Fermentation Quality & Mold Guide]

1. White Surface Film on Miso (Kahm Yeast):
- Appearance: Thin, flat, powdery white film on top of the salt barrier or tamari layer.
- Cause: Aerobic wild yeast (Pichia/Debaryomyces) growing due to oxygen exposure.
- Hazard Level: NON-TOXIC / HARMLESS to miso beneath.
- Action Protocol:
  1. Carefully scrape off and discard the top 1 cm layer of miso and salt.
  2. Wipe the inside jar walls above the miso line with 70% food-grade alcohol on clean paper towel.
  3. Sprinkle a fresh 3mm layer of pure sea salt across the surface.
  4. Reapply clean parchment paper + weight press to seal out oxygen.

2. Green, Blue, or Black Mold (Penicillium / Aspergillus / Stachybotrys):
- Appearance: Fuzzy, raised green/blue/black colonies with musty odor.
- Cause: Moisture pocket + oxygen exposure + contamination.
- Action Protocol:
  1. If isolated to surface (< 1 cm spot): Carefully scoop out mold spot with 3 cm margin. Sanitize rim with alcohol. Re-salt.
  2. If mold has penetrated deeper than 2 cm or if discoloration/foul odor permeates the batch: QUARANTINE jar, notify Chef Gaya, and condemn batch per Food Safety Protocol.

3. Vinegar Mother (Mycoderma Aceti) vs Mold:
- A smooth, gelatinous, rubbery white/translucent raft floating on vinegar is the beneficial "Mother of Vinegar" (Acetobacter cellulose). DO NOT REMOVE or discard; it drives the acetification process.
- If fuzzy dry mold appears on TOP of the mother, the vinegar ABV may be too low (< 4%) or oxygen flow is contaminated.""",
        "metadata": {
            "kahm_yeast": "White flat powdery film - Harmless - Scrape 1cm + wipe alcohol + re-salt",
            "colored_mold": "Green/Black/Blue - Isolate - Scrape 3cm or condemn if deep",
            "vinegar_mother": "Gelatinous translucent mat - Beneficial - Do not discard"
        }
    }
]

def init_db_schema():
    """Create knowledge_documents table with JSONB vector embedding storage."""
    db = SessionLocal()
    try:
        logger.info("Creating production.knowledge_documents table...")
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS production.knowledge_documents (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                category VARCHAR(50) NOT NULL,
                product_name VARCHAR(100),
                title VARCHAR(255) NOT NULL,
                content TEXT NOT NULL,
                embedding JSONB,
                metadata JSONB,
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc')
            );
            CREATE INDEX IF NOT EXISTS idx_knowledge_category ON production.knowledge_documents(category);
            CREATE INDEX IF NOT EXISTS idx_knowledge_product ON production.knowledge_documents(product_name);
        """))
        db.commit()
        logger.info("Table production.knowledge_documents verified/created.")
    except Exception as e:
        db.rollback()
        logger.error(f"Schema init error: {e}")
        raise
    finally:
        db.close()

def seed_knowledge():
    """Generate embeddings and upsert all knowledge items."""
    db = SessionLocal()
    try:
        for idx, item in enumerate(KNOWLEDGE_ITEMS, 1):
            logger.info(f"[{idx}/{len(KNOWLEDGE_ITEMS)}] Embedding '{item['title']}'...")
            emb = get_embedding(item["content"])
            
            # Check if exists by title
            exists = db.execute(
                text("SELECT id FROM production.knowledge_documents WHERE title=:t"),
                {"t": item["title"]}
            ).fetchone()
            
            if exists:
                logger.info(f"Updating existing item: {item['title']}")
                db.execute(
                    text("""
                        UPDATE production.knowledge_documents
                        SET category=:c, product_name=:p, content=:cnt, embedding=:emb, metadata=:meta
                        WHERE id=:id
                    """),
                    {
                        "id": exists[0],
                        "c": item["category"],
                        "p": item["product_name"],
                        "cnt": item["content"],
                        "emb": json.dumps(emb),
                        "meta": json.dumps(item["metadata"])
                    }
                )
            else:
                logger.info(f"Inserting new item: {item['title']}")
                db.execute(
                    text("""
                        INSERT INTO production.knowledge_documents (category, product_name, title, content, embedding, metadata)
                        VALUES (:c, :p, :t, :cnt, :emb, :meta)
                    """),
                    {
                        "c": item["category"],
                        "p": item["product_name"],
                        "t": item["title"],
                        "cnt": item["content"],
                        "emb": json.dumps(emb),
                        "meta": json.dumps(item["metadata"])
                    }
                )
            db.commit()
        logger.info("All Ground Up knowledge items embedded and stored successfully!")
    except Exception as e:
        db.rollback()
        logger.error(f"Seeding error: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    init_db_schema()
    seed_knowledge()
