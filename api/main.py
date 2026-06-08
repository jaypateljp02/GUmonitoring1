# Compatibility shim: Render's start command references api.main:app
# Re-export the FastAPI app from the renamed backend module
from backend.main import app
