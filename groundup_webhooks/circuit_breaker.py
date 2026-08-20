"""
Circuit Breaker Implementation for Ground Up WhatsApp API.
Tracks consecutive failures and opens circuit after 10 failures to prevent spam and resource exhaustion.
"""
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from groundup_webhooks.models import CircuitBreaker
from groundup_webhooks.config import CIRCUIT_BREAKER_MAX_FAILURES

logger = logging.getLogger("groundup_webhooks.circuit_breaker")

SERVICE_NAME = "whatsapp_api"

def is_circuit_open(db: Session) -> bool:
    """Check if the circuit is currently open (tripped)."""
    cb = db.query(CircuitBreaker).filter(CircuitBreaker.service_name == SERVICE_NAME).first()
    if cb and cb.is_open:
        logger.warning("Circuit breaker is OPEN. Outgoing WhatsApp notifications paused.")
        return True
    return False

def record_success(db: Session):
    """Record a successful API dispatch — resets failure count to 0."""
    try:
        cb = db.query(CircuitBreaker).filter(CircuitBreaker.service_name == SERVICE_NAME).first()
        if cb:
            if cb.failure_count > 0 or cb.is_open:
                cb.failure_count = 0
                cb.is_open = False
                cb.updated_at = datetime.utcnow()
                db.commit()
                logger.info("Circuit breaker reset to CLOSED after successful WhatsApp API call.")
    except Exception as e:
        logger.error(f"Error recording circuit breaker success: {e}")
        db.rollback()

def record_failure(db: Session, error_msg: str = ""):
    """Record an API failure. If failures hit 10, trip circuit open."""
    try:
        cb = db.query(CircuitBreaker).filter(CircuitBreaker.service_name == SERVICE_NAME).first()
        if not cb:
            cb = CircuitBreaker(service_name=SERVICE_NAME, failure_count=1, is_open=False)
            db.add(cb)
        else:
            cb.failure_count += 1
            cb.last_failure = datetime.utcnow()
            cb.updated_at = datetime.utcnow()
            if cb.failure_count >= CIRCUIT_BREAKER_MAX_FAILURES:
                cb.is_open = True
                logger.error(f"🚨 CIRCUIT BREAKER TRIPPED OPEN! {cb.failure_count} consecutive WhatsApp failures. Last error: {error_msg}")
        db.commit()
    except Exception as e:
        logger.error(f"Error recording circuit breaker failure: {e}")
        db.rollback()

def reset_circuit(db: Session):
    """Manually reset circuit breaker to closed (e.g. from admin panel)."""
    cb = db.query(CircuitBreaker).filter(CircuitBreaker.service_name == SERVICE_NAME).first()
    if cb:
        cb.failure_count = 0
        cb.is_open = False
        cb.updated_at = datetime.utcnow()
        db.commit()
        logger.info("Circuit breaker manually reset.")
