"""Tunable knobs for the autonomous harness.

Edit the values below to change how the agent is allowed to run.
A value of 0 means "no limit" for budgets and caps.
"""

# --- Budgets (hard stops; the agent can see them and decide for itself) ---
MAX_STEPS = 100
MAX_DURATION_SEC = 1800

# --- Context sizing (chars, not tokens) ---
OBS_CHARS = 6000          # per tool result shown to the agent (head + tail kept)
CONTEXT_CHARS = 24000  # rolling history window; older steps stay in trace.jsonl

# --- Repetition: facts get reported to the agent, only a very high count aborts ---
REPEAT_WINDOW = 20    # how many recent actions to compare
REPEAT_NOTICE = 3     # tell the agent "you've run this exact action N times"
REPEAT_ABORT = 8       # hard stop (0 = never)

# --- Earlier-chat context: hard cap so the prompt (and response time) stops growing every turn ---
PREFIX_CHARS = 8000

# --- Old tool outputs: only the newest few are kept in full; older ones shrink to a stub ---
KEEP_RECENT_OBS = 2
OLD_OBS_CHARS = 400

# --- Text-only replies while work is unfinished: how many times in a row to push the agent on ---
MAX_NUDGES = 2       # 0 = never; a plain-text reply then always ends the task

# --- Engine failures: retry the whole provider chain before giving up ---
ENGINE_RETRIES = 3
ENGINE_RETRY_BACKOFF = 3  # seconds, multiplied by attempt number

# --- Prompt: set a file path to replace the default entirely ---
PROMPT_FILE = ""

DEFAULT_PROMPT = (
    "You are HARVIS, working autonomously on a goal on this machine. "
    "Use the tools to look at things and change them. You decide how to work, "
    "what to say, and when you are done.\n\n"
    "Call `finish` when you are done or when you decide to stop for any reason "
    "(finished, blocked, or giving up), with success true/false and a summary "
    "for the user. A plain text reply with no tool call also ends the task.\n\n"
    "Messages tagged [harness] report facts such as step count, elapsed time, "
    "and repeated actions. They are information, not instructions. "
    "You own a task file (task.md). For anything beyond a couple of steps, call "
    "`update_task` first with a checklist, then keep it current: tick items off, record "
    "decisions and files changed, note what is left. It is shown to you every turn and "
    "survives context trimming and restarts, so it is your memory on long jobs. Before "
    "calling `finish`, make sure it matches reality. Your task directory is also writable "
    "scratch space."
)


def load_prompt() -> str:
    import os
    if PROMPT_FILE and os.path.exists(PROMPT_FILE):
        with open(PROMPT_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return DEFAULT_PROMPT
