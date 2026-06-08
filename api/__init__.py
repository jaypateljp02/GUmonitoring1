# Compatibility shim: Render's start command references api.main
# Re-export the app from the renamed backend module
from backend.main import app
