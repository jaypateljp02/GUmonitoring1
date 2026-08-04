import os
import sys
import psycopg2
from dotenv import load_dotenv
import httpx

# Ensure print uses utf-8 or replaces chars
sys.stdout.reconfigure(encoding='utf-8')

print("=== COLD ROOM MONITORING SYSTEM DIAGNOSTIC CHECK ===")

# 1. Load configuration
load_dotenv()
load_dotenv("backend/.env")
db_url = os.getenv("DATABASE_URL")
print(f"Loaded DATABASE_URL: {db_url}")

# 2. Check Database connectivity
try:
    conn = psycopg2.connect(db_url)
    cursor = conn.cursor()
    cursor.execute("SELECT version();")
    db_version = cursor.fetchone()
    print(f"DB CONNECTION SUCCESS: PostgreSQL Version: {db_version[0]}")
    
    # Check tables count
    cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='monitoring';")
    tables = [t[0] for t in cursor.fetchall()]
    print(f"Ingested monitoring schema tables: {tables}")
    
    cursor.execute("SELECT COUNT(*) FROM monitoring.sensors;")
    sensors_count = cursor.fetchone()[0]
    print(f"Total active sensors registered in database: {sensors_count}")
    
    cursor.execute("SELECT COUNT(*) FROM auth.users;")
    users_count = cursor.fetchone()[0]
    print(f"Total factory users registered in database: {users_count}")
    
    conn.close()
except Exception as e:
    print(f"DB CONNECTION FAILURE: {e}")

# 3. Check eWeLink Credentials & Coolkit Cloud API
email = os.getenv("EWELINK_EMAIL")
password = os.getenv("EWELINK_PASSWORD")
region = os.getenv("EWELINK_REGION", "as")

if email and password:
    print(f"Validating eWeLink cloud configuration for user: {email}")
    try:
        # Import EwelinkClient manually to test login
        sys.path.append(os.path.abspath("."))
        from backend.services.ewelink import EwelinkClient
        client = EwelinkClient(email, password, region)
        import asyncio
        loop = asyncio.new_event_loop()
        login_ok = loop.run_until_complete(client.login())
        print(f"eWeLink Cloud Authentication Check: {'SUCCESS' if login_ok else 'FAILED'}")
        if login_ok:
            devices = loop.run_until_complete(client.get_all_devices())
            print(f"Total IoT devices synced from eWeLink account: {len(devices) if devices else 0}")
    except Exception as e:
        print(f"eWeLink Authentication Exception: {e}")
else:
    print("eWeLink credentials missing in config.")

# 4. Check Production URL Health
base = "https://gu-monitoring.initiativesewafoundation.com"
print(f"Auditing production endpoints at: {base}")
try:
    r = httpx.get(f"{base}/health", timeout=8.0)
    print(f"Production Health Endpoint: Status {r.status_code}")
    
    r = httpx.get(f"{base}/rooms", timeout=8.0)
    print(f"Production Rooms Endpoint: Status {r.status_code} (Found {len(r.json())} active rooms)")
    
    r = httpx.get(f"{base}/monitoring/dashboard", timeout=8.0)
    print(f"Production Live Dashboard Telemetry: Status {r.status_code} (Found {len(r.json().get('live_devices', []))} active telemetry logs)")
except Exception as e:
    print(f"Production URL Check Failed: {e}")

print("=== DIAGNOSTICS COMPLETE ===")
