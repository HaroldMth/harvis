import threading
import time
import uuid
import logging

from tools.desktop import notify

logger = logging.getLogger("harvis.tools")

_timers = {}
_lock = threading.Lock()


def _format_duration(seconds):
    seconds = int(seconds)
    if seconds >= 3600:
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        parts = [f"{h}h"] if h else []
        if m:
            parts.append(f"{m}m")
        if s or not parts:
            parts.append(f"{s}s")
        return " ".join(parts)
    if seconds >= 60:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s" if s else f"{m}m"
    return f"{seconds}s"


def _run_timer(timer_id, seconds, label):
    end_time = time.time() + seconds
    while time.time() < end_time:
        with _lock:
            entry = _timers.get(timer_id)
            if not entry or entry.get("cancelled"):
                return
        time.sleep(min(0.5, end_time - time.time()))

    with _lock:
        entry = _timers.get(timer_id)
        if not entry or entry.get("cancelled"):
            return
        entry["finished"] = True

    title = label or "HARVIS Timer"
    message = f"Timer done ({_format_duration(seconds)})"
    notify(title=title, message=message)


def timer(action: str, seconds: int = None, label: str = None, timer_id: str = None):
    if action == "start":
        if seconds is None or int(seconds) <= 0:
            return {"success": False, "error": "Seconds must be a positive integer."}
        seconds = int(seconds)
        timer_id = str(uuid.uuid4())[:8]
        entry = {
            "id": timer_id,
            "seconds": seconds,
            "label": label or "",
            "started_at": time.time(),
            "cancelled": False,
            "finished": False,
        }
        thread = threading.Thread(
            target=_run_timer,
            args=(timer_id, seconds, label),
            daemon=True,
        )
        entry["thread"] = thread
        with _lock:
            _timers[timer_id] = entry
        thread.start()
        desc = _format_duration(seconds)
        name = f" '{label}'" if label else ""
        return {
            "success": True,
            "output": f"Timer{name} set for {desc} (id: {timer_id}).",
        }

    if action == "cancel":
        if not timer_id:
            return {"success": False, "error": "timer_id is required to cancel a timer."}
        with _lock:
            entry = _timers.get(timer_id)
            if not entry:
                return {"success": False, "error": f"Timer '{timer_id}' not found."}
            if entry.get("finished"):
                return {"success": False, "error": f"Timer '{timer_id}' already finished."}
            entry["cancelled"] = True
        return {"success": True, "output": f"Timer '{timer_id}' cancelled."}

    if action == "list":
        with _lock:
            active = []
            for tid, entry in _timers.items():
                if entry.get("cancelled") or entry.get("finished"):
                    continue
                remaining = max(0, int(entry["seconds"] - (time.time() - entry["started_at"])))
                if remaining <= 0:
                    continue
                name = f" '{entry['label']}'" if entry.get("label") else ""
                active.append(f"- {tid}{name}: {_format_duration(remaining)} remaining")
        if not active:
            return {"success": True, "output": "No active timers."}
        return {"success": True, "output": "Active timers:\n" + "\n".join(active)}

    return {"success": False, "error": f"Unknown timer action: {action}"}
