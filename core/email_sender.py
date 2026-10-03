import smtplib
import ssl
import requests
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from .config import config


def build_message(to_email, subject, body, *, html_body=None, message_id=None, reply_to="", your_name=None):
    from .websites import EMAIL_RE
    if not EMAIL_RE.fullmatch(to_email) or not body.strip() or not subject.strip() or any(c in subject for c in '\r\n'):
        raise ValueError('A valid recipient, single-line subject and non-empty body are required.')
    msg = EmailMessage()
    msg['Subject'], msg['From'], msg['To'] = subject, formataddr((your_name or config.YOUR_NAME, config.GMAIL_ADDRESS)), to_email
    msg['Date'] = formatdate(localtime=False)
    msg['Message-ID'] = message_id or make_msgid(domain=config.GMAIL_ADDRESS.rsplit('@',1)[-1])
    if reply_to:
        msg['In-Reply-To'], msg['References'] = reply_to, reply_to
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body,subtype='html')
    return msg


def check_authentication():
    with smtplib.SMTP_SSL('smtp.gmail.com',465,timeout=25,context=ssl.create_default_context()) as server:
        server.login(config.GMAIL_ADDRESS,config.GMAIL_APP_PASSWORD.replace(' ',''))
        server.noop()
    return True


def deliver(message):
    """A lost response after SMTP starts is uncertain, never an automatic retry."""
    server, started, accepted = None, False, False
    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com',465,timeout=25,context=ssl.create_default_context())
        server.login(config.GMAIL_ADDRESS,config.GMAIL_APP_PASSWORD.replace(' ',''))
        started = True
        refused = server.send_message(message,from_addr=config.GMAIL_ADDRESS,to_addrs=[message['To']])
        accepted = not refused
        codes = [int(value[0]) for value in refused.values()] if refused else []
        return {'state':'sent' if accepted else 'failed','reason':'' if accepted else 'Recipient refused by SMTP.',
                'retryable':bool(codes) and all(400 <= code < 500 for code in codes)}
    except (smtplib.SMTPRecipientsRefused,smtplib.SMTPDataError,smtplib.SMTPSenderRefused,smtplib.SMTPAuthenticationError) as exc:
        codes = [int(value[0]) for value in exc.recipients.values()] if isinstance(exc,smtplib.SMTPRecipientsRefused) else [getattr(exc,'smtp_code',0)]
        transient = bool(codes) and all(400 <= code < 500 for code in codes)
        return {'state':'failed','reason':f'SMTP rejected the operation ({type(exc).__name__}).','retryable':transient}
    except (OSError,smtplib.SMTPException) as exc:
        return {'state':'uncertain' if started else 'failed',
                'reason':f'Connection interrupted ({type(exc).__name__}); check Sent mail before retrying.',
                'retryable':not started}
    finally:
        if server is not None:
            try:
                server.quit()
            except (OSError,smtplib.SMTPException):
                server.close()


def send_email(to_email: str, subject: str, body: str) -> bool:
    # Kept for callers outside the app. The app uses its durable outreach ledger.
    return deliver(build_message(to_email,subject,body))['state']=='sent'


def notify_discord(text: str):
    if not config.DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(config.DISCORD_WEBHOOK_URL, json={"content": text[:1900]}, timeout=10)
    except Exception:
        pass
