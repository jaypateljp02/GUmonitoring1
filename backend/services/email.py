"""Email service module using smtplib."""
import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Union

logger = logging.getLogger(__name__)

def send_html_email(subject: str, html_body: str, recipients: Union[str, List[str]]):
    """
    Send an HTML formatted email using SMTP configuration from environment variables.
    Recipients can be a single email string or a list of emails.
    """
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASSWORD")
    
    if not smtp_user or not smtp_pass:
        logger.warning("SMTP credentials (SMTP_USER/SMTP_PASSWORD) are not set. Cannot send email.")
        return False

    # Normalize recipients into a list
    if isinstance(recipients, str):
        recipient_list = [r.strip() for r in recipients.split(",") if r.strip()]
    else:
        recipient_list = [r.strip() for r in recipients if r.strip()]

    if not recipient_list:
        logger.warning("No recipients specified for email sending.")
        return False

    try:
        # Create message container
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = smtp_user
        msg['To'] = ", ".join(recipient_list)

        # Attach HTML body
        part = MIMEText(html_body, 'html')
        msg.attach(part)

        # Connect to server
        if smtp_port == 465:
            # SSL Connection
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15.0)
        else:
            # STARTTLS Connection
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=15.0)
            server.ehlo()
            server.starttls()
            server.ehlo()

        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, recipient_list, msg.as_string())
        server.quit()
        
        logger.info(f"Email report successfully sent to: {msg['To']}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email via SMTP ({smtp_host}:{smtp_port}): {e}", exc_info=True)
        return False
