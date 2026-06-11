"""Monitoring API main entry point."""
# pyrefly: ignore [missing-import]
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
import subprocess
import json
from datetime import datetime, timedelta
from backend.config import APP_NAME, APP_VERSION
from backend.routes import sensors, rooms, alerts, monitoring
from backend.database import SessionLocal, ensure_db_ready
from backend.models import Room, Sensor, SensorReading, Alert, DeviceTelemetry
import threading
import os
import logging
from backend.worker import start_worker

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title=APP_NAME, version=APP_VERSION, docs_url="/docs", redoc_url="/redoc")

@app.on_event("startup")
def startup_event():
    # 1. Create schemas + tables
    try:
        ensure_db_ready()
        logger.info("DB init complete.")
    except Exception as e:
        logger.error(f"DB init error: {e}")

    # 2. Seed 3 devices (idempotent)
    DEVICES = [
        {"device_id": "a4b002884e", "name": "Device 1 - Temperature", "type": "temperature", "min_threshold": 0.0, "max_threshold": 4.0},
        {"device_id": "a4b002884e", "name": "Device 1 - Humidity",    "type": "humidity",    "min_threshold": None, "max_threshold": None},
        {"device_id": "a4b002898f", "name": "Miso Room - Temperature", "type": "temperature", "min_threshold": 0.0, "max_threshold": 4.0},
        {"device_id": "a4b002898f", "name": "Miso Room - Humidity",    "type": "humidity",    "min_threshold": None, "max_threshold": None},
        {"device_id": "a4b0028991", "name": "Vinegar Room - Temperature", "type": "temperature", "min_threshold": 0.0, "max_threshold": 4.0},
        {"device_id": "a4b0028991", "name": "Vinegar Room - Humidity",    "type": "humidity",    "min_threshold": None, "max_threshold": None},
    ]
    try:
        db = SessionLocal()
        for d in DEVICES:
            exists = db.query(Sensor).filter(Sensor.device_id == d["device_id"], Sensor.type == d["type"]).first()
            if not exists:
                s = Sensor(
                    device_id=d["device_id"],
                    name=d["name"],
                    type=d["type"],
                    min_threshold=d["min_threshold"],
                    max_threshold=d["max_threshold"],
                    active=True
                )
                db.add(s)
        db.commit()
        db.close()
        logger.info("Sensors seeded OK.")
    except Exception as e:
        logger.error(f"Seed error: {e}")

    # 3. Start worker
    worker_thread = threading.Thread(target=start_worker, daemon=True)
    worker_thread.start()

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

app.include_router(sensors.router)
app.include_router(rooms.router)
app.include_router(alerts.router)
app.include_router(monitoring.router)

@app.get("/health", tags=["Dashboard"])
def dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "..", "web", "index.html")
    return FileResponse(html_path, media_type="text/html")


@app.get("/floorplan.jpg", tags=["Dashboard"])
def get_floorplan():
    img_path = os.path.join(os.path.dirname(__file__), "..", "web", "floorplan.jpg")
    if os.path.exists(img_path):
        return FileResponse(img_path, media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="Floor plan image not found")


# Cache for dynamic EAS build APK URL redirect
APK_CACHE = {
    "url": None,
    "last_fetched": datetime.min
}

def update_apk_url_cache():
    global APK_CACHE
    try:
        mobile_dir = os.path.join(os.path.dirname(__file__), "..", "mobile-shared")
        res = subprocess.run(
            ["npx", "eas", "build:list", "--platform", "android", "--limit", "1", "--json", "--non-interactive"],
            cwd=mobile_dir,
            capture_output=True,
            text=True,
            shell=True
        )
        if res.returncode == 0:
            stdout = res.stdout
            if "[" in stdout:
                stdout = stdout[stdout.index("["):]
            data = json.loads(stdout)
            if data and data[0].get("status") == "FINISHED":
                url = data[0].get("artifacts", {}).get("buildUrl")
                if url:
                    APK_CACHE["url"] = url
                    APK_CACHE["last_fetched"] = datetime.utcnow()
                    logger.info(f"Updated APK cache URL from EAS: {url}")
    except Exception as e:
        logger.error(f"Failed to update APK cache from EAS: {e}")

@app.get("/download/apk", tags=["App"])
def download_apk(background_tasks: BackgroundTasks):
    global APK_CACHE
    # Refresh cache in background if older than 5 minutes
    if datetime.utcnow() - APK_CACHE["last_fetched"] > timedelta(minutes=5):
        background_tasks.add_task(update_apk_url_cache)
        
    if APK_CACHE["url"]:
        return RedirectResponse(url=APK_CACHE["url"])
        
    # Fallback to local compiled file
    apk_path = os.path.join(os.path.dirname(__file__), "..", "web", "app.apk")
    if os.path.exists(apk_path):
        return FileResponse(
            apk_path,
            media_type="application/vnd.android.package-archive",
            filename="GUMonitoring.apk"
        )
        
    # If no cache and no local file, attempt synchronous fetch
    update_apk_url_cache()
    if APK_CACHE["url"]:
        return RedirectResponse(url=APK_CACHE["url"])
        
    raise HTTPException(
        status_code=404,
        detail="APK file not found. Cloud compilation may still be in progress."
    )

