from pathlib import Path
from dotenv import load_dotenv
import os

# One loader: .env in the project root first, then ~/.tokens (if present) on top.
load_dotenv(Path(__file__).resolve().parent / ".env")
load_dotenv(os.path.expanduser("~/.tokens"), override=True)

# Name the assistant uses for you in prompts and messages.
USER_NAME = os.getenv("HARVIS_USER_NAME", "User")


def personalize(text: str) -> str:
    """Swap the placeholder owner name baked into prompt text for USER_NAME."""
    return text.replace("HAROLD", USER_NAME.upper()).replace("Harold", USER_NAME)


GEMINI_API_KEY       = os.getenv("GEMINI")
POLLINATIONS_API_KEY = os.getenv("POLLINATIONS")
GROQ_API_KEY          = os.getenv("GROQ")
OPENROUTER_API_KEY    = os.getenv("OPENROUTER")
CEREBRAS_API_KEY      = os.getenv("CEREBRAS")
MISTRAL_API_KEY       = os.getenv("MISTRAL")
COHERE_API_KEY        = os.getenv("COHERE")
OMNIROUTE_API_KEY     = os.getenv("OMNIROUTE")
OPUS_PROXY_API_KEY    = os.getenv("OPUS_PROXY")

# Directionally Local Config (GPT-6.1 Sol / GPT-6 Luna / DeepSeek)
DL_CONFIG_PATHS = [
    os.path.expanduser("~/.config/autopilot/config.json"),
    os.path.expanduser("~/.config_hanstech1/autopilot/config.json"),
]

def get_dl_accounts(force_refresh=False):
    """Discovers all connected Directionally Local accounts and auto-refreshes expired tokens."""
    accounts = []
    for cfg_path in DL_CONFIG_PATHS:
        if not os.path.exists(cfg_path):
            continue
        try:
            import json as _json
            import time as _time
            import requests as _requests
            import base64 as _base64

            with open(cfg_path) as _f:
                _cfg = _json.load(_f)
            dl_info = _cfg.get("providers", {}).get("directionally-local", {})
            api_key = dl_info.get("apiKey")
            refresh_token = dl_info.get("refreshToken")
            expires_at = dl_info.get("expiresAt", 0)
            now_ms = int(_time.time() * 1000)

            login = "unknown"
            if api_key:
                parts = api_key.split(".")
                if len(parts) > 1:
                    pad = "=" * (-len(parts[1]) % 4)
                    try:
                        claims = _json.loads(_base64.urlsafe_b64decode(parts[1] + pad))
                        login = claims.get("login", "unknown")
                    except Exception:
                        pass

            # If expired (< 60s left) or force_refresh requested, auto-refresh token
            if (force_refresh or (expires_at and expires_at - now_ms < 60000)) and refresh_token:
                resp = _requests.post(
                    "https://free.directionallylocal.com/v1/token/refresh",
                    json={"refresh_token": refresh_token},
                    headers={"Content-Type": "application/json"},
                    timeout=10
                )
                if resp.status_code == 200:
                    data = resp.json()
                    api_key = data.get("access_token")
                    refresh_token = data.get("refresh_token") or refresh_token
                    expires_in = data.get("expires_in", 3600)
                    dl_info["apiKey"] = api_key
                    dl_info["refreshToken"] = refresh_token
                    dl_info["expiresAt"] = now_ms + (expires_in * 1000)
                    _cfg.setdefault("providers", {})["directionally-local"] = dl_info
                    with open(cfg_path, "w") as _f:
                        _json.dump(_cfg, _f, indent=2)
            if api_key:
                accounts.append({"login": login, "apiKey": api_key, "path": cfg_path, "refresh_token": refresh_token})
        except Exception:
            pass
    return accounts

def _get_dl_api_key(force_refresh=False):
    token = os.getenv("DIRECTIONALLY_LOCAL_API_KEY")
    if token and not force_refresh:
        return token
    accs = get_dl_accounts(force_refresh=force_refresh)
    return accs[0]["apiKey"] if accs else None

DIRECTIONALLY_LOCAL_API_KEY = _get_dl_api_key()

# Gmail Config
GMAIL_USER            = os.getenv("GMAIL_USER", os.getenv("GMAIL"))
GMAIL_APP_PASSWORD    = os.getenv("GMAIL_APP_PASSWORD", os.getenv("GMAIL_PASS"))

# Telegram Bot Config
TELEGRAM_BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", os.getenv("TELEGRAM"))
raw_users             = os.getenv("TELEGRAM_ALLOWED_USERS", "")
TELEGRAM_ALLOWED_USERS = [
    int(u.strip()) if u.strip().isdigit() else u.strip()
    for u in raw_users.split(",") if u.strip()
]
