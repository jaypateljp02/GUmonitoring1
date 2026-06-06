"""Monitoring API main entry point."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from api.config import APP_NAME, APP_VERSION
from api.routes import rooms, sensors, alerts
from api.database import SessionLocal, ensure_db_ready
from api.models import Room, Sensor, SensorReading, Alert, DeviceTelemetry
import threading
import os
import logging
from api.worker import start_worker

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

app.include_router(rooms.router)
app.include_router(sensors.router)
app.include_router(alerts.router)

@app.get("/", tags=["Dashboard"])
def dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    return FileResponse(html_path, media_type="text/html")
