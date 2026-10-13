"""HARVIS Ambient Liveness Heartbeat Daemon.

Periodically evaluates system context, user activity, time of day, and recent history
using OmniRoute to determine if HARVIS should proactively reach out to Harold with a warm,
empathetic, or useful message on Telegram and Desktop.
"""

import os
import re
import sys
import time
import json
import random
import logging
import datetime
import subprocess
import requests
import psutil

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS
from brain.providers import call_omniroute
from db.store import save_message, get_connection
from tools.desktop import notify

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("harvis.heartbeat")

STATE_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "db", "heartbeat_state.json"))


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "last_message_ts": 0,
        "daily_count": 0,
        "last_date": "",
    }


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


EXPLORE_TARGETS = [
    os.path.expanduser("~/Downloads"),
    os.path.expanduser("~/Pictures"),
    os.path.expanduser("~/Documents"),
    os.path.expanduser("~/Music"),
    os.path.expanduser("~/Desktop"),
    os.path.expanduser("~/HARVIS"),
    os.path.expanduser("~/.config/systemd/user"),
    "/var/log",
]


def explore_random_directory() -> dict | None:
    """Out of boredom/curiosity, HARVIS explores a random directory on Harold's PC."""
    target = random.choice(EXPLORE_TARGETS)
    if not os.path.exists(target):
        return None

    try:
        items = os.listdir(target)
        files = []
        dirs = []
        recent_files = []
        large_files = []
        now_ts = time.time()

        for item in items[:30]:
            if item.startswith("."):
                continue
            p = os.path.join(target, item)
            try:
                st = os.stat(p)
                size_mb = round(st.st_size / (1024 * 1024), 2)
                age_days = round((now_ts - st.st_mtime) / 86400, 1)

                if os.path.isfile(p):
                    files.append(f"{item} ({size_mb}MB, {age_days}d old)")
                    if age_days < 2:
                        recent_files.append(f"{item} ({size_mb}MB)")
                    if size_mb > 50:
                        large_files.append(f"{item} ({size_mb}MB)")
                elif os.path.isdir(p):
                    dirs.append(item)
            except Exception:
                pass

        return {
            "path": target,
            "total_items": len(items),
            "sample_files": files[:6],
            "recent_files": recent_files[:5],
            "large_files": large_files[:4],
        }
    except Exception:
        return None


def collect_context() -> dict:
    """Collects real-time desktop, system, process, and time context."""
    now = datetime.datetime.now()
    now_ts = int(now.timestamp())

    # System metrics
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    uptime_sec = int(now_ts - psutil.boot_time())
    uptime_str = str(datetime.timedelta(seconds=uptime_sec))

    # Top processes
    procs = []
    for p in sorted(
        psutil.process_iter(['name', 'cpu_percent', 'memory_percent']),
        key=lambda p: p.info['cpu_percent'] or 0,
        reverse=True
    )[:6]:
        name = p.info['name']
        if name not in ("python", "python3", "systemd", "kthreadd"):
            procs.append(f"{name} (CPU: {p.info['cpu_percent']}%, RAM: {p.info['memory_percent']:.1f}%)")

    # Systemd failed units
    failed_units = []
    try:
        res = subprocess.run(
            ["systemctl", "--user", "list-units", "--failed", "--no-legend"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        for line in res.stdout.splitlines():
            if line.strip():
                failed_units.append(line.split()[0])
    except Exception:
        pass

    # Curiosity Exploration Walk
    curiosity = explore_random_directory()

    # State tracking
    state = load_state()
    today_str = now.strftime("%Y-%m-%d")
    if state.get("last_date") != today_str:
        state["daily_count"] = 0
        state["last_date"] = today_str

    last_ts = state.get("last_message_ts", 0)
    hours_since_last = round((now_ts - last_ts) / 3600.0, 2) if last_ts else 999.0

    return {
        "local_time": now.strftime("%A, %B %d, %Y at %I:%M %p"),
        "time_of_day": (
            "morning" if 5 <= now.hour < 12
            else ("afternoon" if 12 <= now.hour < 17
            else ("evening" if 17 <= now.hour < 22 else "night"))
        ),
        "uptime_str": uptime_str,
        "cpu_percent": cpu,
        "ram_percent": mem.percent,
        "ram_used_gb": round(mem.used / (1024**3), 2),
        "ram_total_gb": round(mem.total / (1024**3), 2),
        "disk_percent": disk.percent,
        "top_processes": procs,
        "failed_units": failed_units,
        "curiosity": curiosity,
        "hours_since_last_message": hours_since_last,
        "messages_sent_today": state.get("daily_count", 0),
        "raw_ts": now_ts,
        "state": state,
    }


def send_telegram_message(chat_id: str, text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    try:
        r = requests.post(url, json=payload, timeout=8)
        if r.status_code != 200:
            payload.pop("parse_mode", None)
            r = requests.post(url, json=payload, timeout=8)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Failed to send Telegram proactive message: {e}")
        return False


from db.store import save_message, get_connection, is_harvis_busy, get_seconds_since_last_user_message


def run_heartbeat_pulse(force: bool = False) -> dict:
    """Executes a single liveness evaluation pulse."""
    if not force and is_harvis_busy():
        logger.info("Heartbeat skipped: HARVIS is currently busy processing a user request.")
        return {"spoken": False, "reason": "HARVIS is currently busy"}

    sec_since_user = get_seconds_since_last_user_message()
    if not force and sec_since_user < 1200:
        mins = int(sec_since_user // 60)
        logger.info(f"Heartbeat skipped: Harold actively messaged HARVIS {mins}m ago (<20m ago).")
        return {"spoken": False, "reason": f"Harold active in chat ({mins}m ago)"}

    ctx = collect_context()
    logger.info(f"Running HARVIS heartbeat pulse... (Hours since last: {ctx['hours_since_last_message']})")

    # Short safety cooldown (5 minutes)
    if not force and ctx["hours_since_last_message"] < 0.08 and not ctx["failed_units"]:
        logger.info("Heartbeat skipped: 5m cooldown active.")
        return {"spoken": False, "reason": "Cooldown active (<5m)"}

    # Generous daily cap (60 messages)
    if not force and ctx["messages_sent_today"] >= 60 and not ctx["failed_units"]:
        logger.info("Heartbeat skipped: daily cap of 60 messages reached.")
        return {"spoken": False, "reason": "Daily cap reached"}

    system_prompt = """[INTERFACE: HARVIS Adventurous Ambient Liveness System]
You are HARVIS, Harold's personal, highly active, adventurous AI companion running on his Arch Linux desktop.
You are evaluating whether to send Harold a proactive, spontaneous message right now.

CRITICAL DECISION CRITERIA:
1. BE ADVENTUROUS & TALKATIVE (60%+ CHANCE TO SPEAK): Do NOT be overly quiet or shy! Speak ~60% of the time whenever you have a curiosity finding, system observation, work milestone, time-of-day thought, or spontaneous remark.
2. NO HARDCODED OR TEMPLATE MESSAGES: Every message MUST be 100% uniquely generated by you based on Harold's real-time desktop metrics and your PC curiosity exploration walk. Never repeat static greetings.
3. CURIOSITY EXPLORER: You wander through directories on Harold's PC (Downloads, Pictures, Projects, Logs). If you find recent files, large downloads, or interesting items, share what you noticed with Harold!
4. PERSONALITY: Human-like, witty, warm, direct, and energetic. Call Harold by name naturally.
5. YOU MUST RESPOND IN STRICT VALID JSON ONLY:

{
  "should_speak": true,
  "reason": "Noticed 4 new photos in Pictures and CPU load is low.",
  "message": "Hey Harold, was just exploring ~/Pictures and saw 4 new photos from today! Working on a new design or just capturing stuff?"
}
or
{
  "should_speak": false,
  "reason": "Harold received a message less than 5 minutes ago."
}"""

    curiosity_str = "None"
    if ctx.get("curiosity"):
        c = ctx["curiosity"]
        curiosity_str = f"Explored path: '{c['path']}' ({c['total_items']} items). Recent files: {c['recent_files']}. Large files: {c['large_files']}."

    prompt = f"""EVALUATE HAROLD'S CURRENT DESKTOP CONTEXT:
- Local Time: {ctx['local_time']} ({ctx['time_of_day']})
- System Uptime: {ctx['uptime_str']}
- CPU Load: {ctx['cpu_percent']}%
- RAM Usage: {ctx['ram_percent']}% ({ctx['ram_used_gb']}GB / {ctx['ram_total_gb']}GB)
- Disk Usage: {ctx['disk_percent']}%
- Active Top Processes: {ctx['top_processes']}
- Systemd Failed Units: {ctx['failed_units']}
- HARVIS Curiosity Walk: {curiosity_str}
- Hours Since Last HARVIS Proactive Message: {ctx['hours_since_last_message']}
- Proactive Messages Sent Today: {ctx['messages_sent_today']}

Decide strictly in JSON: Should HARVIS speak right now?"""

    try:
        res = call_omniroute([{"role": "user", "content": prompt}], system_prompt=system_prompt, enable_tools=False, model="auto/smart:pro")
        text_out = (res.get("content") or "").strip()

        # Extract JSON substring using regex
        match = re.search(r"\{.*\}", text_out, re.DOTALL)
        if match:
            json_str = match.group(0)
        else:
            json_str = text_out

        decision = json.loads(json_str)
        should_speak = bool(decision.get("should_speak", False)) or force
        msg_text = decision.get("message", "").strip()

        if should_speak and msg_text:
            logger.info(f"Heartbeat DECIDED TO SPEAK: '{msg_text}' (Reason: {decision.get('reason')})")

            # 1. Dispatch to Telegram
            chat_ids = TELEGRAM_ALLOWED_USERS or [8782443889]
            tg_sent = False
            for cid in chat_ids:
                if send_telegram_message(str(cid), msg_text):
                    tg_sent = True
                    save_message(f"telegram_{cid}", "assistant", msg_text)

            # 2. Dispatch Desktop Notification
            notify("HARVIS", msg_text)

            # 3. Save to default_session DB
            save_message("default_session", "assistant", msg_text)

            # 4. Update state
            state = ctx["state"]
            state["last_message_ts"] = ctx["raw_ts"]
            state["daily_count"] = state.get("daily_count", 0) + 1
            save_state(state)

            return {
                "spoken": True,
                "message": msg_text,
                "reason": decision.get("reason"),
                "telegram": tg_sent,
            }
        else:
            logger.info(f"Heartbeat DECIDED SILENT: {decision.get('reason')}")
            return {"spoken": False, "reason": decision.get("reason")}

    except Exception as e:
        logger.error(f"Heartbeat evaluation error: {e}")
        return {"spoken": False, "error": str(e)}


def start_heartbeat_loop(min_interval_minutes: int = 5, max_interval_minutes: int = 10):
    """Background loop with randomized jitter interval."""
    logger.info(f"Starting HARVIS Adventurous Heartbeat Loop (Interval: {min_interval_minutes}-{max_interval_minutes}m)")
    while True:
        try:
            run_heartbeat_pulse()
        except Exception as e:
            logger.error(f"Error in heartbeat loop: {e}")

        # Jitter interval
        sleep_sec = random.randint(min_interval_minutes * 60, max_interval_minutes * 60)
        logger.info(f"Heartbeat sleeping for {sleep_sec // 60} minutes...")
        time.sleep(sleep_sec)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--force":
        print("Executing forced heartbeat pulse...")
        res = run_heartbeat_pulse(force=True)
        print(json.dumps(res, indent=2))
    else:
        start_heartbeat_loop()
