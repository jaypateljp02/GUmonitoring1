"""Monitoring API main entry point."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from api.config import APP_NAME, APP_VERSION, CORS_ORIGINS, DEBUG
from api.routes import rooms, sensors, alerts
from api.database import engine, Base, SessionLocal
from api.models import room, sensor, reading, alert, device_telemetry
from api.models.sensor import Sensor
from sqlalchemy import text
import threading
import os
import logging
from api.worker import start_worker

logger = logging.getLogger(__name__)

app = FastAPI(title=APP_NAME, version=APP_VERSION, docs_url="/docs", redoc_url="/redoc")

@app.on_event("startup")
def startup_event():
    # Create schema and all tables on fresh cloud database
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE SCHEMA IF NOT EXISTS monitoring"))
            conn.execute(text("CREATE SCHEMA IF NOT EXISTS auth"))
            conn.commit()
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully.")
    except Exception as e:
        logger.error(f"Error creating tables: {e}")

    # Seed the 3 devices if they don't exist
    DEVICES = [
        {"device_id": "a4b002884e", "name": "Device 1 - Temperature", "type": "temperature", "min_threshold": 0.0, "max_threshold": 4.0},
        {"device_id": "a4b002884e", "name": "Device 1 - Humidity",    "type": "humidity",    "min_threshold": None, "max_threshold": None},
        {"device_id": "a4b002898f", "name": "Miso Room - Temperature", "type": "temperature", "min_threshold": 0.0, "max_threshold": 4.0},
        {"device_id": "a4b002898f", "name": "Miso Room - Humidity",    "type": "humidity",    "min_threshold": None, "max_threshold": None},
        {"device_id": "a4b0028991", "name": "Vinegar Room - Temperature", "type": "temperature", "min_threshold": 0.0, "max_threshold": 4.0},
        {"device_id": "a4b0028991", "name": "Vinegar Room - Humidity",    "type": "humidity",    "min_threshold": None, "max_threshold": None},
    ]
    try:
        with SessionLocal() as db:
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
            logger.info("Sensor devices seeded successfully.")
    except Exception as e:
        logger.error(f"Error seeding devices: {e}")

    # Start background worker
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

