from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import subprocess
import json
from datetime import datetime, timedelta
from jose import jwt
import bcrypt
from sqlalchemy.orm import Session
from sqlalchemy import text
import sys
import threading
import os
import logging

from backend.config import APP_NAME, APP_VERSION, JWT_SECRET, JWT_ALGORITHM
from backend.routes import sensors, rooms, alerts, monitoring, reports
from backend.database import SessionLocal, ensure_db_ready, get_db
from backend.models import Room, Sensor, SensorReading, Alert, DeviceTelemetry, User
from backend.schemas import LoginRequest, LoginResponse, UserResponseModel
from backend.services.insights import generate_report_html
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
        import traceback
        logger.error(f"DB init error: {e}")
        logger.error(traceback.format_exc())

    # 2. Seed 3 devices (idempotent)
    DEVICES = [
        {"device_id": "a4b002884e", "name": "Device 1 - Temperature", "type": "temperature", "min_threshold": 0.0, "max_threshold": 4.0},
        {"device_id": "a4b002884e", "name": "Device 1 - Humidity",    "type": "humidity",    "min_threshold": None, "max_threshold": None},
        {"device_id": "a4b002898f", "name": "Miso Room - Temperature", "type": "temperature", "min_threshold": 0.0, "max_threshold": 4.0},
        {"device_id": "a4b002898f", "name": "Miso Room - Humidity",    "type": "humidity",    "min_threshold": None, "max_threshold": None},
        {"device_id": "REMOVED_a4b0028991", "name": "Vinegar Room - Temperature", "type": "temperature", "min_threshold": 0.0, "max_threshold": 4.0},
        {"device_id": "REMOVED_a4b0028991", "name": "Vinegar Room - Humidity",    "type": "humidity",    "min_threshold": None, "max_threshold": None},
    ]
    try:
        logger.info("Opening session to seed sensors...")
        db = SessionLocal()
        for d in DEVICES:
            logger.info(f"Checking sensor device_id={d['device_id']}, type={d['type']}...")
            exists = db.query(Sensor).filter(Sensor.device_id == d["device_id"], Sensor.type == d["type"]).first()
            logger.info(f"Sensor check result: {exists is not None}")
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
        logger.info("Committing seeded sensors...")
        db.commit()
        db.close()
        logger.info("Sensors seeded OK.")
    except Exception as e:
        logger.error(f"Seed error: {e}")

    # 3. Start worker
    disable_worker = os.getenv("DISABLE_WORKER", "false").lower() == "true"
    if not disable_worker:
        worker_thread = threading.Thread(target=start_worker, daemon=True)
        worker_thread.start()
    else:
        print("Worker thread disabled via DISABLE_WORKER=true")

from starlette.middleware.base import BaseHTTPMiddleware


class ReportPreviewMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path.lower()
        if "preview" in path or path in ["/reports", "/reports/", "/report", "/report/"]:
            db = SessionLocal()
            try:
                html_body, _, _ = await generate_report_html(db)
                return HTMLResponse(content=html_body)
            except Exception as e:
                logger.error(f"Middleware error generating report preview: {e}", exc_info=True)
            finally:
                db.close()
        return await call_next(request)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(ReportPreviewMiddleware)


app.include_router(sensors.router)
app.include_router(rooms.router)
app.include_router(alerts.router)
app.include_router(monitoring.router)
app.include_router(reports.router)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False

@app.post("/auth/login", response_model=LoginResponse, tags=["Authentication"])
def login(request: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate user and return JWT access token."""
    try:
        user = db.query(User).filter(
            User.email == request.email.strip().lower(),
            User.active == True
        ).first()

        if not user or not verify_password(request.password, user.password):
            raise HTTPException(
                status_code=401,
                detail="Invalid email or password",
            )

        # Expire in 24 hours
        expire = datetime.utcnow() + timedelta(hours=24)
        payload = {
            "sub": str(user.id),
            "role": user.role,
            "name": user.name,
            "exp": expire,
            "iat": datetime.utcnow(),
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

        return LoginResponse(
            access_token=token,
            user=UserResponseModel(
                id=user.id,
                name=user.name,
                email=user.email,
                role=user.role
            )
        )
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Login error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal server error"
        )

@app.get("/", tags=["Dashboard"])
@app.get("/health", tags=["Dashboard"])
@app.get("/dashboard", tags=["Dashboard"])
@app.get("/index.html", tags=["Dashboard"], include_in_schema=False)
def serve_dashboard():
    """Serve the main Web Dashboard."""
    html_path = os.path.join(os.path.dirname(__file__), "..", "web", "index.html")
    if os.path.exists(html_path):
        response = FileResponse(html_path, media_type="text/html")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response
    raise HTTPException(status_code=404, detail="Dashboard index.html not found")

@app.get("/reports/preview", response_class=HTMLResponse, tags=["Reports"])
@app.get("/sensors/reports/preview", response_class=HTMLResponse, tags=["Reports"])
@app.get("/api/reports/preview", response_class=HTMLResponse, tags=["Reports"])
@app.get("/api/sensors/reports/preview", response_class=HTMLResponse, tags=["Reports"])
async def preview_report_alias(db: Session = Depends(get_db)):
    """Serve the detailed daily report preview page for any WhatsApp button URL variant."""
    try:
        html_body, _, _ = await generate_report_html(db)
        return HTMLResponse(content=html_body)
    except Exception as e:
        logger.error(f"Error in preview_report_alias: {e}", exc_info=True)
        return HTMLResponse(content=f"<h3>Error generating report preview: {str(e)}</h3>", status_code=500)

@app.get("/version", tags=["Dashboard"])
def get_version():
    apk_path = os.path.join(os.path.dirname(__file__), "..", "app", "android", "app", "build", "outputs", "apk", "release", "app-release.apk")
    size = os.path.getsize(apk_path) if os.path.exists(apk_path) else -1
    return {
        "version": "apk-update-v1",
        "apk_size_bytes": size,
        "apk_exists": os.path.exists(apk_path)
    }

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
def download_apk():
    return RedirectResponse(url="https://storage.googleapis.com/groundup-499909.appspot.com/monitoring-app.apk")

@app.get("/download/tasks-apk", tags=["App"])
def download_tasks_apk():
    return RedirectResponse(url="https://storage.googleapis.com/groundup-499909.appspot.com/tasks-app.apk")

@app.get("/download/admin-apk", tags=["App"])
def download_admin_apk():
    return RedirectResponse(url="https://storage.googleapis.com/groundup-499909.appspot.com/admin-app.apk")

@app.get("/app.apk", tags=["Frontend"])
def serve_apk():
    apk_path = os.path.join(os.path.dirname(__file__), "..", "web", "app.apk")
    if os.path.exists(apk_path):
        return FileResponse(
            apk_path,
            media_type="application/vnd.android.package-archive",
            filename="ground-up-monitor.apk"
        )
    raise HTTPException(status_code=404, detail="APK not found")

@app.head("/app.apk", include_in_schema=False)
def serve_apk_head():
    apk_path = os.path.join(os.path.dirname(__file__), "..", "web", "app.apk")
    if os.path.exists(apk_path):
        return FileResponse(
            apk_path,
            media_type="application/vnd.android.package-archive",
            filename="ground-up-monitor.apk"
        )
    raise HTTPException(status_code=404, detail="APK not found")

# Mount web directory for static assets
web_dir = os.path.join(os.path.dirname(__file__), "..", "web")
if os.path.exists(web_dir):
    app.mount("/web", StaticFiles(directory=web_dir), name="web")
