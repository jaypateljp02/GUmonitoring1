import httpx
import threading
import logging

logger = logging.getLogger(__name__)

def _post_webhook(url: str, payload: dict):
    try:
        with httpx.Client() as client:
            response = client.post(url, json=payload, timeout=5.0)
            logger.info(f"Webhook POST to {url} completed with status: {response.status_code}")
    except Exception as e:
        logger.error(f"Error sending webhook to {url}: {e}")

def trigger_webhook(url: str, payload: dict):
    """Fire and forget webhook trigger via a daemon thread."""
    logger.info(f"Triggering background webhook thread for: {url}")
    thread = threading.Thread(target=_post_webhook, args=(url, payload), daemon=True)
    thread.start()
