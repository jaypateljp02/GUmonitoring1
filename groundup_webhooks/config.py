"""
Config settings for groundup-webhooks shared module.
Single source of truth for WhatsApp Cloud API credentials, database settings, and service defaults.
"""
import os

# ─── Database ───────────────────────────────────────────────────────────────────
DEFAULT_LOCAL_URL = "postgresql://postgres:1234@localhost:5432/groundup"
DEFAULT_PROD_URL = "postgresql://assurement:Arnav%402541@localhost:5432/groundupfactory"

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    try:
        import psycopg2
        conn = psycopg2.connect(DEFAULT_LOCAL_URL, connect_timeout=1)
        conn.close()
        DATABASE_URL = DEFAULT_LOCAL_URL
    except Exception:
        DATABASE_URL = DEFAULT_PROD_URL

# ─── Production Domain & Callback URL ───────────────────────────────────────────
GUBANDHU_DOMAIN = "https://gubandhu.initiativesewafoundation.com"
WEBHOOK_CALLBACK_URL = f"{GUBANDHU_DOMAIN}/webhook/whatsapp"

# ─── WhatsApp Business API (Single Source of Truth) ─────────────────────────────
# All apps (monitoring, admin, tasks, production) MUST use these values.
META_API_VERSION = "v23.0"
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "1147295148477347")
WHATSAPP_BUSINESS_ACCOUNT_ID = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "3959830240990246")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "initiative2026")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "EAAOzmGwwNGcBSC4I8psGr0YaZBJl2qvaTX8yfP1HSjDyLAsqUFwZAxmcEwHRgBnkOVh2b5eYewv2SBKpR3d2zsQqNVZAPlNAZBYZAWTnZCMNLhU8LFxTz3IlUdFVFetpTHUNjZAWHO0Oo7JFugI6WZAesWGFAoATAcHM1i5cut689SMY6eNP4eZA2ZCPyPcZB5wrXi6SQZDZD")

# ─── Gemini API (for AI features) ───────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", ""))


# ─── Circuit Breaker ────────────────────────────────────────────────────────────
CIRCUIT_BREAKER_MAX_FAILURES = 10
