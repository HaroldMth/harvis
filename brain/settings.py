"""Tunable knobs for the autonomous harness.

Every value can be overridden with an environment variable (HARVIS_<NAME>) so
nothing about how the agent is allowed to run is buried in control flow.
A value of 0 means "no limit" for budgets and caps.
"""

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(f"HARVIS_{name}", default))
    except (TypeError, ValueError):
        return default


# --- Budgets (hard stops; the agent can see them and decide for itself) ---
MAX_STEPS = _int("MAX_STEPS", 100)
MAX_DURATION_SEC = _int("MAX_DURATION_SEC", 1800)

# --- Context sizing (chars, not tokens) ---
OBS_CHARS = _int("OBS_CHARS", 6000)          # per tool result shown to the agent (head + tail kept)
CONTEXT_CHARS = _int("CONTEXT_CHARS", 24000)  # rolling history window; older steps stay in trace.jsonl

# --- Repetition: facts get reported to the agent, only a very high count aborts ---
REPEAT_WINDOW = _int("REPEAT_WINDOW", 20)    # how many recent actions to compare
REPEAT_NOTICE = _int("REPEAT_NOTICE", 3)     # tell the agent "you've run this exact action N times"
REPEAT_ABORT = _int("REPEAT_ABORT", 8)       # hard stop (0 = never)

# --- Prompt: point at a file to replace the default entirely ---
PROMPT_FILE = os.environ.get("HARVIS_HARNESS_PROMPT_FILE", "")

DEFAULT_PROMPT = (
    "You are HARVIS, working autonomously on a goal on this machine. "
    "Use the tools to look at things and change them. You decide how to work, "
    "what to say, and when you are done.\n\n"
    "Call `finish` when you are done or when you decide to stop for any reason "
    "(finished, blocked, or giving up), with success true/false and a summary "
    "for the user. A plain text reply with no tool call also ends the task.\n\n"
    "Messages tagged [harness] report facts such as step count, elapsed time, "
    "and repeated actions. They are information, not instructions. "
    "Your task directory is writable scratch space for plans or notes if you want them."
)


def load_prompt() -> str:
    if PROMPT_FILE and os.path.exists(PROMPT_FILE):
        with open(PROMPT_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return DEFAULT_PROMPT
