"""HARVIS Tool Plugin: gmail

Provides Gmail integration for HARVIS to read, search, summarize unread inbox emails via IMAP
and compose/send emails via SMTP using App Password credentials configured in ~/.tokens.
"""

import os
import sys
import json
import imaplib
import smtplib
import email
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from config import GMAIL_USER, GMAIL_APP_PASSWORD
except ImportError:
    GMAIL_USER = os.getenv("GMAIL_USER") or os.getenv("GMAIL")
    GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD") or os.getenv("GMAIL_PASS")

PLUGIN_SCHEMA = [
    {
        "name": "check_emails",
        "description": "Check, search, or summarize recent emails from Gmail inbox. Returns subject, sender, date, and body preview.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of emails to return (default: 5)"
                },
                "unread_only": {
                    "type": "boolean",
                    "description": "If true, fetches only unread emails (default: true)"
                },
                "search_query": {
                    "type": "string",
                    "description": "Optional search term, sender, or subject to filter emails (e.g. 'FROM google', 'SUBJECT urgent')"
                }
            },
            "required": []
        }
    },
    {
        "name": "send_email",
        "description": "Send an email via Gmail SMTP.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient email address"
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject"
                },
                "body": {
                    "type": "string",
                    "description": "Email body content"
                }
            },
            "required": ["to", "subject", "body"]
        }
    }
]


def _decode_str(val):
    if not val:
        return ""
    try:
        decoded_list = decode_header(val)
        parts = []
        for content, encoding in decoded_list:
            if isinstance(content, bytes):
                parts.append(content.decode(encoding or "utf-8", errors="replace"))
            else:
                parts.append(str(content))
        return "".join(parts)
    except Exception:
        return str(val)


def _get_credentials():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.expanduser("~/.tokens"), override=True)
    except Exception:
        pass
    user = os.getenv("GMAIL_USER") or os.getenv("GMAIL")
    passwd = os.getenv("GMAIL_APP_PASSWORD") or os.getenv("GMAIL_PASS")
    return user, passwd


def check_emails_handler(args=None):
    user, passwd = _get_credentials()
    if not user or not passwd:
        return {
            "success": False,
            "error": "Gmail credentials not configured in ~/.tokens. Please add GMAIL_USER='your@gmail.com' and GMAIL_APP_PASSWORD='xxxx xxxx xxxx xxxx' to ~/.tokens."
        }

    args = args or {}
    limit = args.get("limit", 5)
    unread_only = args.get("unread_only", False)
    search_query = args.get("search_query", "").strip()

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(user, passwd)
        mail.select("inbox")

        if search_query:
            status, messages = mail.search(None, f'TEXT "{search_query}"')
        elif unread_only:
            status, messages = mail.search(None, 'UNSEEN')
        else:
            status, messages = mail.search(None, 'ALL')

        if status != "OK":
            mail.logout()
            return {"success": False, "error": "IMAP search failed."}

        email_ids = messages[0].split()
        if not email_ids and unread_only and not search_query:
            # Fall back to ALL recent if no unread
            status, messages = mail.search(None, 'ALL')
            email_ids = messages[0].split()

        email_ids = email_ids[-limit:]
        results = []

        for e_id in reversed(email_ids):
            _, msg_data = mail.fetch(e_id, '(RFC822)')
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    subject = _decode_str(msg.get("Subject"))
                    from_sender = _decode_str(msg.get("From"))
                    date_str = _decode_str(msg.get("Date"))

                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            content_type = part.get_content_type()
                            content_disposition = str(part.get("Content-Disposition"))
                            if content_type == "text/plain" and "attachment" not in content_disposition:
                                body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                                break
                    else:
                        body = msg.get_payload(decode=True).decode("utf-8", errors="replace")

                    preview = body.strip().replace("\r", "").replace("\n", " ")[:250]

                    results.append({
                        "id": e_id.decode("utf-8"),
                        "subject": subject,
                        "from": from_sender,
                        "date": date_str,
                        "preview": preview
                    })

        mail.logout()
        data = {
            "count": len(results),
            "unread_only": unread_only,
            "emails": results
        }
        return {
            "success": True,
            "output": json.dumps(data, indent=2)
        }

    except Exception as e:
        return {"success": False, "error": f"Gmail IMAP error: {str(e)}"}


def send_email_handler(args):
    user, passwd = _get_credentials()
    if not user or not passwd:
        return {
            "success": False,
            "error": "Gmail credentials not configured in ~/.tokens. Please add GMAIL_USER='your@gmail.com' and GMAIL_APP_PASSWORD='xxxx xxxx xxxx xxxx' to ~/.tokens."
        }

    if isinstance(args, str):
        return {"success": False, "error": "Arguments must be a dictionary with 'to', 'subject', 'body'."}

    to_addr = args.get("to")
    subject = args.get("subject")
    body = args.get("body")

    if not to_addr or not subject or not body:
        return {"success": False, "error": "Missing required fields: to, subject, body."}

    try:
        msg = MIMEMultipart()
        msg["From"] = user
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(user, passwd)
        server.sendmail(user, [to_addr], msg.as_string())
        server.quit()

        return {
            "success": True,
            "output": f"Email successfully sent to {to_addr} with subject '{subject}'."
        }
    except Exception as e:
        return {"success": False, "error": f"Gmail SMTP error: {str(e)}"}


# Generic handler dispatch for plugin registry
def handler(args):
    if isinstance(args, dict) and "action" in args:
        action = args["action"]
        if action == "send":
            return send_email_handler(args)
        return check_emails_handler(args)
    return check_emails_handler(args)
