from dotenv import load_dotenv
import os

load_dotenv(os.path.expanduser("~/.tokens"))

GEMINI_API_KEY       = os.getenv("GEMINI")
POLLINATIONS_API_KEY = os.getenv("POLLINATIONS")
GROQ_API_KEY          = os.getenv("GROQ")
OPENROUTER_API_KEY    = os.getenv("OPENROUTER")
CEREBRAS_API_KEY      = os.getenv("CEREBRAS")
MISTRAL_API_KEY       = os.getenv("MISTRAL")
COHERE_API_KEY        = os.getenv("COHERE")
OMNIROUTE_API_KEY     = os.getenv("OMNIROUTE")
OPUS_PROXY_API_KEY    = os.getenv("OPUS_PROXY", "ak-NyUBMMR5MdA1SFhFV5dx1a_P20bOQ3xV")

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
