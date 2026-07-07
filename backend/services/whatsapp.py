import os
import logging
import threading
import httpx
from typing import List, Optional

logger = logging.getLogger(__name__)

def calculate_priority(room_type: str, sensor_type: str, value: float, sensor) -> str:
    """
    Calculate alert priority based on room type, sensor type, and severity of threshold deviation.
    """
    room_type_lower = (room_type or "").lower()
    sensor_type_lower = (sensor_type or "").lower()

    if sensor_type_lower == "offline":
        if room_type_lower in ["fridge", "freezer"]:
            return "High"
        return "Low"

    # For threshold violations (temperature, humidity)
    if room_type_lower in ["fridge", "freezer"]:
        if sensor_type_lower == "temperature":
            # If temperature deviates by more than 5 degrees C, set to Critical
            min_th = float(sensor.min_threshold) if sensor.min_threshold is not None else None
            max_th = float(sensor.max_threshold) if sensor.max_threshold is not None else None
            
            val_float = float(value)
            if max_th is not None and val_float > (max_th + 5.0):
                return "Critical"
            if min_th is not None and val_float < (min_th - 5.0):
                return "Critical"
            return "High"
        else:
            # Humidity or other sensors in fridges/freezers
            return "High"
    elif room_type_lower == "room":
        return "Medium"
    
    return "Medium"

def _dispatch_whatsapp_request(phone_number_id: str, access_token: str, recipient: str, payload: dict):
    """
    Synchronous worker function to send the HTTP POST request to Meta API.
    """
    url = f"https://graph.facebook.com/v23.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    try:
        with httpx.Client() as client:
            response = client.post(url, json=payload, headers=headers, timeout=10.0)
            if response.status_code in [200, 201]:
                logger.info(f"WhatsApp message sent successfully to {recipient}. Response: {response.text}")
            else:
                logger.error(f"Failed to send WhatsApp message to {recipient}. Status: {response.status_code}, Response: {response.text}")
    except Exception as e:
        logger.error(f"Exception raised while sending WhatsApp to {recipient}: {e}", exc_info=True)

def send_whatsapp_template_to_all(template_name: str, body_parameters: List[str], button_parameter: Optional[str] = None):
    """
    Formats the payload and dispatches the WhatsApp template message to all configured recipients
    using a non-blocking background thread.
    """
    access_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    recipients_raw = os.getenv("WHATSAPP_RECIPIENTS")

    if not access_token or not phone_number_id or not recipients_raw:
        logger.warning("WhatsApp API variables are not fully configured in environment (.env). Skipping notification.")
        return

    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]
    if not recipients:
        logger.warning("No WhatsApp recipients found in WHATSAPP_RECIPIENTS.")
        return

    for recipient in recipients:
        # Build payload
        payload = {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {
                    "code": "en"
                },
                "components": []
            }
        }

        # Body components
        body_comp = {
            "type": "body",
            "parameters": [{"type": "text", "text": str(param)} for param in body_parameters]
        }
        payload["template"]["components"].append(body_comp)

        # Dynamic URL Button component (DETAILS button)
        if button_parameter:
            button_comp = {
                "type": "button",
                "sub_type": "url",
                "index": "0",
                "parameters": [
                    {
                        "type": "text",
                        "text": str(button_parameter)
                    }
                ]
            }
            payload["template"]["components"].append(button_comp)

        logger.info(f"Queueing WhatsApp template '{template_name}' for {recipient}")
        
        # Fire background thread to avoid blocking the caller
        thread = threading.Thread(
            target=_dispatch_whatsapp_request,
            args=(phone_number_id, access_token, recipient, payload),
            daemon=True
        )
        thread.start()

def send_whatsapp_alert(
    sensor_name: str,
    alert_type: str,
    current_value: str,
    normal_range: str,
    duration: str,
    priority: str,
    alert_id: str
):
    """
    Send the fermentary_alert_v1 template to all recipients.
    Dynamic Data:
    - Sensor Name
    - Alert Type
    - Current Value
    - Normal Range
    - Duration
    - Priority
    """
    body_params = [
        sensor_name,
        alert_type,
        current_value,
        normal_range,
        duration,
        priority
    ]
    
    send_whatsapp_template_to_all(
        template_name="fermentary_alert_v1",
        body_parameters=body_params,
        button_parameter="health"
    )

def send_whatsapp_daily_summary(
    date_str: str,
    normal_count: int,
    active_count: int,
    highest_priority: str,
    ai_summary: str
):
    """
    Send the fermentary_daily_summary_v1 template to all recipients.
    Dynamic Data:
    - Date
    - Sensors Normal Count
    - Active Alerts Count
    - Highest Priority
    - AI Summary
    """
    body_params = [
        date_str,
        str(normal_count),
        str(active_count),
        highest_priority,
        ai_summary
    ]
    
    send_whatsapp_template_to_all(
        template_name="fermentary_daily_summary_v1",
        body_parameters=body_params,
        button_parameter="reports/preview"
    )
