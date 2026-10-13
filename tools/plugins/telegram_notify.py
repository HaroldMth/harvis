"""HARVIS Tool Plugin: send_telegram

Allows HARVIS to send direct Telegram messages, photos, audio, voice notes, and files to Harold's phone/Telegram app on demand.
"""

import os
import sys
import requests

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS
except ImportError:
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM")
    TELEGRAM_ALLOWED_USERS = [8782443889]

from db.store import save_message

PLUGIN_SCHEMA = {
    "name": "send_telegram",
    "description": "Send direct Telegram messages, media, photos, audio/mp3, voice notes, or files/documents to Harold's phone/Telegram app.",
    "parameters": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The text message or caption to accompany the file/media (optional if file_path is provided)."
            },
            "file_path": {
                "type": "string",
                "description": "Path to a local file, photo, image, screenshot, audio/mp3, or document to send."
            },
            "media_type": {
                "type": "string",
                "enum": ["auto", "photo", "audio", "voice", "document"],
                "description": "Type of media being sent ('photo', 'audio', 'voice', 'document', or 'auto' to infer from extension). Defaults to 'auto'."
            }
        },
        "required": []
    }
}


def _detect_media_type(file_path: str, requested: str = "auto") -> str:
    if requested and requested != "auto":
        return requested
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"):
        return "photo"
    if ext in (".mp3", ".m4a", ".flac", ".wav", ".aac"):
        return "audio"
    if ext in (".ogg", ".oga", ".opus"):
        return "voice"
    return "document"


def handler(args):
    token = TELEGRAM_BOT_TOKEN or os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM")
    if not token:
        return {"success": False, "error": "TELEGRAM_BOT_TOKEN is not configured in ~/.tokens."}

    text = ""
    file_path = None
    media_type = "auto"

    if isinstance(args, dict):
        text = args.get("message", "")
        file_path = args.get("file_path")
        media_type = args.get("media_type", "auto")
    else:
        text = str(args)

    if file_path:
        file_path = os.path.expanduser(file_path.strip())
        if not os.path.exists(file_path):
            return {"success": False, "error": f"File not found: {file_path}"}

    if not text and not file_path:
        return {"success": False, "error": "Either 'message' or 'file_path' must be provided."}

    chat_ids = TELEGRAM_ALLOWED_USERS or [8782443889]
    base_url = f"https://api.telegram.org/bot{token}"
    success_count = 0

    for cid in chat_ids:
        try:
            if not file_path:
                # Text-only message
                url = f"{base_url}/sendMessage"
                payload = {"chat_id": cid, "text": text, "parse_mode": "Markdown"}
                r = requests.post(url, json=payload, timeout=10)
                if r.status_code != 200:
                    payload.pop("parse_mode", None)
                    r = requests.post(url, json=payload, timeout=10)
                if r.status_code == 200:
                    success_count += 1
                    save_message(f"telegram_{cid}", "assistant", text)
            else:
                mtype = _detect_media_type(file_path, media_type)
                endpoint_map = {
                    "photo": "sendPhoto",
                    "audio": "sendAudio",
                    "voice": "sendVoice",
                    "document": "sendDocument",
                }
                endpoint = endpoint_map.get(mtype, "sendDocument")
                field_map = {
                    "photo": "photo",
                    "audio": "audio",
                    "voice": "voice",
                    "document": "document",
                }
                field_name = field_map.get(mtype, "document")
                url = f"{base_url}/{endpoint}"

                data = {"chat_id": cid}
                if text:
                    data["caption"] = text
                    data["parse_mode"] = "Markdown"

                with open(file_path, "rb") as f:
                    files = {field_name: f}
                    r = requests.post(url, data=data, files=files, timeout=30)
                    if r.status_code != 200 and "parse_mode" in data:
                        data.pop("parse_mode", None)
                        f.seek(0)
                        r = requests.post(url, data=data, files=files, timeout=30)

                if r.status_code == 200:
                    success_count += 1
                    save_message(f"telegram_{cid}", "assistant", f"[Sent {mtype}: {os.path.basename(file_path)}] {text}".strip())
        except Exception as e:
            pass

    if success_count > 0:
        target_desc = f"file '{file_path}'" if file_path else f"message '{text}'"
        return {"success": True, "output": f"Telegram dispatch succeeded for {target_desc}."}
    return {"success": False, "error": "Failed to send Telegram message/media via Telegram Bot API."}

