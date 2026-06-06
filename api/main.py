"""Monitoring API main entry point."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from api.config import APP_NAME, APP_VERSION, CORS_ORIGINS, DEBUG
from api.routes import rooms, sensors, alerts
import threading
import os
from api.worker import start_worker

app = FastAPI(title=APP_NAME, version=APP_VERSION, docs_url="/docs" if DEBUG else None, redoc_url="/redoc" if DEBUG else None)

@app.on_event("startup")
def startup_event():
    worker_thread = threading.Thread(target=start_worker, daemon=True)
    worker_thread.start()

app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

app.include_router(rooms.router)
app.include_router(sensors.router)
app.include_router(alerts.router)

@app.get("/", tags=["Dashboard"])
def dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    return FileResponse(html_path, media_type="text/html")

