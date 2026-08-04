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
