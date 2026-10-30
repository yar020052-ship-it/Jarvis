"""Email integration for JARVIS — read and send emails via IMAP/SMTP.

Supports Gmail, Outlook, and any IMAP/SMTP server. Credentials are stored
in config/api_keys.json under 'email_*' keys.
"""

import imaplib
import smtplib
import email
from email.message import EmailMessage
from email.header import decode_header
from pathlib import Path
import sys
import json

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import gemini


def _config() -> dict:
    try:
        p = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _imap_connect():
    cfg = _config()
    host = cfg.get("email_imap_host", "")
    port = int(cfg.get("email_imap_port", 993))
    user = cfg.get("email_user", "")
    password = cfg.get("email_password", "")
    if not host or not user or not password:
        return None, "Email не настроен. Укажите email_imap_host, email_user, email_password в config/api_keys.json."
    try:
        conn = imaplib.IMAP4_SSL(host, port)
        conn.login(user, password)
        return conn, None
    except Exception as e:
        return None, f"Не удалось подключиться к IMAP: {e}"


def _smtp_connect():
    cfg = _config()
    host = cfg.get("email_smtp_host", "")
    port = int(cfg.get("email_smtp_port", 587))
    user = cfg.get("email_user", "")
    password = cfg.get("email_password", "")
    if not host or not user or not password:
        return None, "Email не настроен. Укажите email_smtp_host, email_user, email_password в config/api_keys.json."
    try:
        conn = smtplib.SMTP(host, port)
        conn.starttls()
        conn.login(user, password)
        return conn, None
    except Exception as e:
        return None, f"Не удалось подключиться к SMTP: {e}"


def _decode_header_value(value: str) -> str:
    parts = decode_header(value)
    result = ""
    for part, charset in parts:
        if isinstance(part, bytes):
            result += part.decode(charset or "utf-8", errors="replace")
        else:
            result += part
    return result


def read_emails(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Read recent emails from inbox."""
    params = parameters or {}
    folder = params.get("folder", "INBOX")
    count = int(params.get("count", 5))
    unread_only = params.get("unread_only", True)

    conn, err = _imap_connect()
    if err:
        return err

    try:
        conn.select(folder)
        search_criteria = "UNSEEN" if unread_only else "ALL"
        status, messages = conn.search(None, search_criteria)
        if status != "OK":
            return "Не удалось получить список писем."

        msg_ids = messages[0].split()
        if not msg_ids:
            return "Новых писем нет." if unread_only else "Папка пуста."

        # Take the last N
        msg_ids = msg_ids[-count:]
        results = []
        for mid in reversed(msg_ids):
            status, data = conn.fetch(mid, "(RFC822)")
            if status != "OK":
                continue
            raw = data[0][1]
            msg = email.message_from_bytes(raw)
            subject = _decode_header_value(msg.get("Subject", "(без темы)"))
            sender = _decode_header_value(msg.get("From", ""))
            date = msg.get("Date", "")
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode(
                            part.get_content_charset() or "utf-8", errors="replace"
                        )
                        break
            else:
                body = msg.get_payload(decode=True)
                if body:
                    body = body.decode(msg.get_content_charset() or "utf-8", errors="replace")

            results.append(
                f"От: {sender}\nТема: {subject}\nДата: {date}\n{body[:500]}"
            )

        conn.close()
        conn.logout()
        return "\n\n---\n\n".join(results) if results else "Писем не найдено."
    except Exception as e:
        try:
            conn.logout()
        except Exception:
            pass
        return f"Ошибка чтения почты: {e}"


def send_email(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Send an email."""
    params = parameters or {}
    to = params.get("to", "").strip()
    subject = params.get("subject", "").strip()
    body = params.get("body", "").strip()

    if not to:
        return "Укажите адрес получателя (to)."
    if not subject:
        return "Укажите тему письма (subject)."
    if not body:
        return "Укажите текст письма (body)."

    cfg = _config()
    from_addr = cfg.get("email_user", "")
    if not from_addr:
        return "Email не настроен. Укажите email_user в config/api_keys.json."

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    conn, err = _smtp_connect()
    if err:
        return err

    try:
        conn.send_message(msg)
        conn.quit()
        return f"Письмо отправлено на {to}."
    except Exception as e:
        try:
            conn.quit()
        except Exception:
            pass
        return f"Ошибка отправки: {e}"


TOOL = {
    "name": "email",
    "description": "Read and send emails via IMAP/SMTP. Supports Gmail, Outlook, and any IMAP/SMTP server.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "read (read emails) or send (send email)",
            },
            "folder": {
                "type": "STRING",
                "description": "Email folder to read from (default: INBOX)",
            },
            "count": {
                "type": "INTEGER",
                "description": "Number of emails to read (default: 5)",
            },
            "unread_only": {
                "type": "BOOLEAN",
                "description": "Read only unread emails (default: true)",
            },
            "to": {
                "type": "STRING",
                "description": "Recipient email address (for send)",
            },
            "subject": {
                "type": "STRING",
                "description": "Email subject (for send)",
            },
            "body": {
                "type": "STRING",
                "description": "Email body text (for send)",
            },
        },
        "required": ["action"],
    },
    "handler": lambda parameters, response=None, player=None, session_memory=None: (
        read_emails(parameters, response, player, session_memory)
        if (parameters or {}).get("action", "read").lower() == "read"
        else send_email(parameters, response, player, session_memory)
    ),
}