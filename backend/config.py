"""Monitoring API configuration."""
import os
from dotenv import load_dotenv
load_dotenv()

# Render (and some other providers) supply a DATABASE_URL with the legacy
# "postgres://" scheme. SQLAlchemy 2.0 only accepts "postgresql://", so we
# normalise the URL here before it is used anywhere else.
_raw_db_url = os.getenv("DATABASE_URL", "postgresql://postgres:1234@localhost:5432/groundup")
DATABASE_URL = _raw_db_url.replace("postgres://", "postgresql://", 1) if _raw_db_url.startswith("postgres://") else _raw_db_url

JWT_SECRET = os.getenv("JWT_SECRET", "ground-up-dev-secret-change-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
AUTH_API_URL = os.getenv("AUTH_API_URL", "http://localhost:8000")
APP_NAME = "Ground Up Monitoring API"
APP_VERSION = "1.0.0"
DEBUG = os.getenv("DEBUG", "true").lower() == "true"
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
EDGE_API_KEY = os.getenv("EDGE_API_KEY", "factory-tapo-123")

