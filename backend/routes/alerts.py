"""Alert routes."""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.alert import Alert
from backend.middleware.jwt_verify import get_current_user, TokenUser
from backend.schemas import AlertResponse, AlertResolve, MessageResponse
from backend.services.alert_detail import generate_alert_detail_html

router = APIRouter(prefix="/alerts", tags=["Alerts"])


@router.get("", response_model=List[AlertResponse])
def list_alerts(
    resolved: Optional[bool] = Query(None),
    sensor_id: Optional[str] = Query(None),
    db: Session = Depends(get_db), user: TokenUser = Depends(get_current_user)
):
    query = db.query(Alert)
    if resolved is not None:
        query = query.filter(Alert.resolved == resolved)
    if sensor_id is not None:
        query = query.filter(Alert.sensor_id == sensor_id)
    return [AlertResponse.model_validate(a) for a in query.order_by(Alert.created_at.desc()).all()]


@router.get("/count")
def count_alerts(
    resolved: Optional[bool] = Query(False),
    db: Session = Depends(get_db), user: TokenUser = Depends(get_current_user)
):
    return {"count": db.query(Alert).filter(Alert.resolved == resolved).count()}


@router.post("/{alert_id}/resolve_public")
@router.put("/{alert_id}/resolve_public")
def resolve_alert_public(alert_id: str, db: Session = Depends(get_db)):
    """Public endpoint to mark an alert as resolved from the WhatsApp alert detail link."""
    alert = None
    try:
        import uuid
        alert_uuid = uuid.UUID(alert_id)
        alert = db.query(Alert).filter(Alert.id == alert_uuid).first()
    except Exception:
        pass

    if not alert:
        alert = db.query(Alert).filter(Alert.resolved == False).order_by(Alert.created_at.desc()).first()

    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    alert.resolved = True
    db.commit()
    return {"message": "Alert marked as resolved"}


@router.put("/{alert_id}/resolve", response_model=MessageResponse)
def resolve_alert(alert_id: str, db: Session = Depends(get_db), user: TokenUser = Depends(get_current_user)):
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.resolved = True
    db.commit()
    return MessageResponse(message="Alert resolved")


@router.get("/{alert_id}")
def get_alert_detail(alert_id: str, request: Request, db: Session = Depends(get_db)):
    """Serve the alert detail page for WhatsApp alert message links."""
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        try:
            import uuid
            alert_uuid = uuid.UUID(alert_id)
            alert = db.query(Alert).filter(Alert.id == alert_uuid).first()
            if alert:
                return AlertResponse.model_validate(alert)
        except Exception:
            pass
        raise HTTPException(status_code=404, detail="Alert not found")
    
    html_content = generate_alert_detail_html(alert_id, db)
    return HTMLResponse(content=html_content)

