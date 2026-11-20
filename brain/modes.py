"""HARVIS Chat Modes & Engine Definitions.

Simplified 2-Tier Engine Architecture:
1. Default Engine: kr/claude-sonnet-4.5 (Autonomous Driver) with silent failovers
   to kr/deepseek-3.2 and kr/qwen3-coder-next.
2. Sprint Engine (/fast): kr/glm-5 (1.8s Fast Triage/Query) with fallback to kr/minimax-m2.5.
"""

MODES = ("normal", "fast")
DEFAULT_MODE = "normal"

DEFAULT_ENGINE = "gemini-3.5-flash-lite"
DEFAULT_FALLBACKS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "openai/gpt-5.3-codex",
    "openai/gpt-6-astra",
    "qwen/qwen3.8-27b",
]

FAST_ENGINE = "gemini-3.5-flash-lite"
FAST_FALLBACKS = [
    "gemini-3.1-flash-lite",
    "qwen/qwen3.8-27b",
    "codestral-latest",
]

ALIASES = {
    "fast": "fast",
    "quick": "fast",
    "speed": "fast",
    "normal": "normal",
    "default": "normal",
}

MODE_HELP = (
    "🤖 *HARVIS Engine Modes:*\n"
    "• `Default` — Pollinations (`openai/gpt-5.3-codex` ➔ `openai/gpt-6-astra` ➔ `openai/gpt-6.1-sol`) ➔ Gemini (`gemini-3.5-flash`)\n"
    "   Deep verification, tool calling, and self-correcting autonomous coding.\n"
    "   *Failovers:* `qwen/qwen3.8-27b` ➔ `codestral-latest`\n\n"
    "• `/fast <query>` — High-Speed Sprint (`openai/gpt-6.1-sol` ➔ `qwen/qwen3.8-27b` on Groq LPU)\n"
    "   Instant factual answers, rapid lookups, fast status checks.\n"
    "   *Failover:* `gemini-3.5-flash` ➔ `openai/gpt-oss-120b` ➔ `codestral-latest`\n\n"
    "• `/mode` — Show active engine configuration"
)


def get_engine(mode: str = DEFAULT_MODE) -> str:
    """Return primary model ID for given mode."""
    if mode == "fast":
        return FAST_ENGINE
    return DEFAULT_ENGINE


def get_fallback_chain(mode: str = DEFAULT_MODE) -> list[str]:
    """Return list of models to try in sequence for given mode."""
    if mode == "fast":
        return [FAST_ENGINE] + FAST_FALLBACKS
    return [DEFAULT_ENGINE] + DEFAULT_FALLBACKS


def omniroute_options(mode: str, model_override: str = None) -> dict:
    """Generates options dict for OmniRoute call."""
    if model_override:
        return {"model": model_override}
    model = get_engine(mode)
    return {"model": model}


def parse_slash(text: str):
    """Parses leading slash commands from prompt."""
    if not text or not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None

    first, _, rest = stripped.partition(" ")
    cmd = first[1:].lower().split("@", 1)[0]
    rest = rest.strip()

    if cmd == "mode":
        return {"kind": "status"}

    mode = ALIASES.get(cmd)
    if not mode:
        return None

    if rest:
        return {"kind": "oneshot", "mode": mode, "prompt": rest}
    return {"kind": "set", "mode": mode}


def apply_prompt_mode(prompt, mode=DEFAULT_MODE):
    parsed = parse_slash(prompt)
    if parsed and parsed.get("kind") == "oneshot":
        return parsed["mode"], parsed["prompt"]
    return (mode if mode in MODES else DEFAULT_MODE), prompt


def switch_message(mode: str) -> str:
    mode = mode if mode in MODES else DEFAULT_MODE
    engine = get_engine(mode)
    if mode == "fast":
        return f"⚡ Mode switched to **Fast Sprint** (`{engine}` ~1.8s)"
    return f"🛡️ Mode switched to **Default** (`{engine}`)"


def status_message(mode: str) -> str:
    mode = mode if mode in MODES else DEFAULT_MODE
    return f"{switch_message(mode)}\n\n{MODE_HELP}"
