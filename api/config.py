"""Monitoring API configuration."""
import os
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:1234@localhost:5432/groundup")
JWT_SECRET = os.getenv("JWT_SECRET", "ground-up-dev-secret-change-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
AUTH_API_URL = os.getenv("AUTH_API_URL", "http://localhost:8000")
APP_NAME = "Ground Up Monitoring API"
APP_VERSION = "1.0.0"
DEBUG = os.getenv("DEBUG", "true").lower() == "true"
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
