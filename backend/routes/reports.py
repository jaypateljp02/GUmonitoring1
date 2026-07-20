"""Reports router for daily sensor telemetry email delivery."""
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from typing import Optional
import logging

from backend.database import get_db
from backend.services.insights import generate_daily_report, generate_report_html
from backend.config import EDGE_API_KEY
from backend.schemas import MessageResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sensors", tags=["Reports"])

def verify_cron_or_api_key(
    request: Request,
    x_api_key: Optional[str] = Header(None),
    x_appengine_cron: Optional[str] = Header(None)
):
    # App Engine Cron service requests contain X-Appengine-Cron: true
    if x_appengine_cron == "true":
        return
    
    # Otherwise check for API key
    if x_api_key == EDGE_API_KEY:
        return
        
    raise HTTPException(status_code=401, detail="Unauthorized: Invalid API Key or Cron Header")

@router.get("/reports/daily-trigger", response_model=MessageResponse)
@router.post("/reports/daily-trigger", response_model=MessageResponse)
async def trigger_daily_report(
    db: Session = Depends(get_db),
    _ = Depends(verify_cron_or_api_key)
):
    """Trigger the daily report generation and dispatch."""
    try:
        success = await generate_daily_report(db)
        if success:
            return MessageResponse(message="Daily report sent successfully.")
        else:
            return MessageResponse(message="Report run completed, but no report was sent (e.g. no active rooms or email failed).")
    except Exception as e:
        logger.error(f"Error triggering daily report: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate and send report: {str(e)}")

@router.get("/reports/send-test", response_model=MessageResponse)
@router.post("/reports/trigger-daily-summary", response_model=MessageResponse)
@router.get("/reports/trigger-daily-summary", response_model=MessageResponse)
async def send_test_report(
    db: Session = Depends(get_db)
):
    """Developer test endpoint to manually trigger a report send & WhatsApp summary immediately."""
    try:
        success = await generate_daily_report(db)
        if success:
            return MessageResponse(message="Daily summary report generated and dispatched successfully.")
        else:
            raise HTTPException(status_code=500, detail="Failed to send daily summary report. Check server logs.")
    except Exception as e:
        logger.error(f"Error in send_test_report: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/reports/preview", response_class=HTMLResponse)
async def preview_daily_report(
    db: Session = Depends(get_db)
):
    """Preview the daily report HTML template directly in the browser."""
    try:
        html_body, _, _ = await generate_report_html(db)
        return HTMLResponse(content=html_body)
    except Exception as e:
        logger.error(f"Error in preview_daily_report: {e}", exc_info=True)
        return HTMLResponse(content=f"<h3>Error generating preview: {str(e)}</h3>", status_code=500)
