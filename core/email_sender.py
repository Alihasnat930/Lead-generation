import smtplib
import requests
from email.mime.text import MIMEText
from .config import config


def send_email(to_email: str, subject: str, body: str) -> bool:
    if not to_email or not subject or not body:
        return False
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = config.GMAIL_ADDRESS
    msg["To"] = to_email
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
            server.sendmail(config.GMAIL_ADDRESS, [to_email], msg.as_string())
        return True
    except Exception as e:
        print(f"Email send failed for {to_email}: {e}")
        return False


def notify_discord(text: str):
    if not config.DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(config.DISCORD_WEBHOOK_URL, json={"content": text[:1900]}, timeout=10)
    except Exception:
        pass
