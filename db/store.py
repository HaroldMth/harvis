import sqlite3
import os
import json
import time
import threading

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "harvis.db"))
SCHEMA_PATH = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "schema.sql"))

def get_connection():
    # WAL + a long busy timeout: the bot, harness threads and heartbeat all write to this file,
    # and the default (rollback journal, 5s) raises "database is locked" under that load.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except sqlite3.Error:
        pass
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    with open(SCHEMA_PATH, "r") as f:
        schema = f.read()
    cursor.executescript(schema)
    conn.commit()
    conn.close()

BUSY_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "busy_state.json"))


_BUSY_LOCK = threading.Lock()
_BUSY_COUNT = 0
BUSY_STALE_SEC = 600  # a busy flag nobody has refreshed for this long is treated as a crashed task


def _write_busy(count: int):
    try:
        with open(BUSY_FILE, "w") as f:
            json.dump({"busy": count > 0, "count": count, "ts": time.time()}, f)
    except Exception:
        pass


def set_harvis_busy(is_busy: bool):
    """Process-level busy state so the heartbeat stays quiet while HARVIS works.
    Reference-counted: two overlapping tasks no longer clear each other's flag."""
    global _BUSY_COUNT
    with _BUSY_LOCK:
        _BUSY_COUNT = _BUSY_COUNT + 1 if is_busy else max(0, _BUSY_COUNT - 1)
        _write_busy(_BUSY_COUNT)


def touch_harvis_busy():
    """Refresh the busy timestamp. Called every harness step so long tasks stay 'busy'."""
    with _BUSY_LOCK:
        if _BUSY_COUNT > 0:
            _write_busy(_BUSY_COUNT)


def is_harvis_busy() -> bool:
    """Checks if HARVIS is actively processing a user command."""
    if not os.path.exists(BUSY_FILE):
        return False
    try:
        with open(BUSY_FILE, "r") as f:
            data = json.load(f)
        if data.get("busy") and (time.time() - data.get("ts", 0)) < BUSY_STALE_SEC:
            return True
    except Exception:
        pass
    return False


def get_seconds_since_last_user_message() -> float:
    """Returns seconds elapsed since Harold last sent a message to HARVIS."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT strftime('%s', 'now') - strftime('%s', created_at) FROM messages WHERE role = 'user' ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        if row and row[0] is not None:
            return float(row[0])
    except Exception:
        pass
    return 999999.0


def save_message(session_id, role, content):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
        (session_id, role, content)
    )
    conn.commit()
    conn.close()

def get_history(session_id, limit=20):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
        (session_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    # Return in chronological order (oldest first)
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def clear_session(session_id):
    """Wipes all messages for a session — use when history is corrupt."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return count

def log_tool_call(tool_name, args, result, status):
    conn = get_connection()
    cursor = conn.cursor()
    # Serialize args to string if they are a dict or list
    if not isinstance(args, str):
        try:
            args_str = json.dumps(args)
        except Exception:
            args_str = str(args)
    else:
        args_str = args

    if not isinstance(result, str):
        try:
            result_str = json.dumps(result)
        except Exception:
            result_str = str(result)
    else:
        result_str = result

    cursor.execute(
        "INSERT INTO tool_calls (tool_name, args, result, status) VALUES (?, ?, ?, ?)",
        (tool_name, args_str, result_str, status)
    )
    conn.commit()
    conn.close()

def get_cache(prompt_hash):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT response, model FROM cache WHERE prompt_hash = ?",
        (prompt_hash,)
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"response": row[0], "model": row[1]}
    return None

def set_cache(prompt_hash, response, model):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO cache (prompt_hash, response, model) VALUES (?, ?, ?)",
        (prompt_hash, response, model)
    )
    conn.commit()
    conn.close()

def update_provider_status(provider, success: bool):
    import datetime
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    
    cursor.execute("SELECT provider FROM provider_status WHERE provider = ?", (provider,))
    row = cursor.fetchone()
    if row:
        if success:
            cursor.execute(
                "UPDATE provider_status SET last_success = ? WHERE provider = ?",
                (now, provider)
            )
        else:
            cursor.execute(
                "UPDATE provider_status SET last_fail = ? WHERE provider = ?",
                (now, provider)
            )
    else:
        if success:
            cursor.execute(
                "INSERT INTO provider_status (provider, last_success, last_fail) VALUES (?, ?, NULL)",
                (provider, now)
            )
        else:
            cursor.execute(
                "INSERT INTO provider_status (provider, last_success, last_fail) VALUES (?, NULL, ?)",
                (provider, now)
            )
    conn.commit()
    conn.close()

def is_provider_healthy(provider, cooldown_seconds=300):
    """
    Returns True if the provider is healthy or its cooldown period has passed.
    """
    import datetime
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT last_success, last_fail FROM provider_status WHERE provider = ?", (provider,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return True
        
    last_success, last_fail = row
    if not last_fail:
        return True
        
    try:
        fail_dt = datetime.datetime.fromisoformat(last_fail)
        # If we had a success after the failure, it's healthy
        if last_success:
            succ_dt = datetime.datetime.fromisoformat(last_success)
            if succ_dt > fail_dt:
                return True
                
        # Otherwise check if cooldown has elapsed
        elapsed = (datetime.datetime.now() - fail_dt).total_seconds()
        if elapsed > cooldown_seconds:
            return True
        return False
    except Exception:
        # Fallback to healthy if parse error
        return True


# ─── Session Management ───────────────────────────────────────────────────────
import random
import string
import datetime

def generate_session_id() -> str:
    """Generate a unique 4-character lowercase alphanumeric ID."""
    conn = get_connection()
    cursor = conn.cursor()
    chars = string.ascii_lowercase + string.digits
    for _ in range(100):
        sid = "".join(random.choice(chars) for _ in range(4))
        if sid in ("main", "help", "fast", "mode", "stop"):
            continue
        cursor.execute("SELECT id FROM sessions WHERE id = ?", (sid,))
        if not cursor.fetchone():
            conn.close()
            return sid
    conn.close()
    return "".join(random.choice(chars) for _ in range(4))


def get_active_session(user_id: str) -> str:
    """Returns active session ID for user, defaulting to 'general'."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT session_id FROM user_active_session WHERE user_id = ?", (str(user_id),))
    row = cursor.fetchone()
    conn.close()
    if row and row[0]:
        return row[0]
    return "general"


def set_active_session(user_id: str, session_id: str):
    """Sets active session for a user."""
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    cursor.execute(
        "INSERT OR REPLACE INTO user_active_session (user_id, session_id) VALUES (?, ?)",
        (str(user_id), session_id)
    )
    cursor.execute("UPDATE sessions SET last_active = ? WHERE id = ?", (now, session_id))
    conn.commit()
    conn.close()


def create_session(user_id: str, title: str = None, active_dir: str = None, session_id: str = None) -> str:
    """Creates a new session and sets it as active."""
    conn = get_connection()
    cursor = conn.cursor()
    sid = session_id or generate_session_id()
    display_title = title or f"Session {sid}"
    now = datetime.datetime.now().isoformat()
    cursor.execute(
        """INSERT OR REPLACE INTO sessions 
           (id, user_id, title, active_dir, notes, status, created_at, last_active)
           VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
        (sid, str(user_id), display_title, active_dir or "", "", now, now)
    )
    conn.commit()
    conn.close()
    set_active_session(user_id, sid)
    return sid


def list_user_sessions(user_id: str, limit: int = 10) -> list[dict]:
    """Lists recent sessions for user with active indicator and message counts."""
    active_sid = get_active_session(user_id)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT s.id, s.title, s.active_dir, s.notes, s.created_at, s.last_active,
                  (SELECT COUNT(*) FROM messages m WHERE m.session_id LIKE '%' || s.id) as msg_count
           FROM sessions s
           WHERE s.user_id = ? AND s.status = 'active'
           ORDER BY s.last_active DESC LIMIT ?""",
        (str(user_id), limit)
    )
    rows = cursor.fetchall()
    conn.close()
    
    sessions = []
    found_general = False
    for r in rows:
        is_active = (r[0] == active_sid)
        if r[0] == "general":
            found_general = True
        sessions.append({
            "id": r[0],
            "title": r[1],
            "active_dir": r[2],
            "notes": r[3],
            "created_at": r[4],
            "last_active": r[5],
            "msg_count": r[6],
            "is_active": is_active,
        })
    
    # Always include general session if not in list
    if not found_general:
        sessions.append({
            "id": "general",
            "title": "General Scratchpad",
            "active_dir": "",
            "notes": "Default general conversation session",
            "created_at": "",
            "last_active": "",
            "msg_count": 0,
            "is_active": (active_sid == "general"),
        })
    return sessions


def get_session_info(session_id: str) -> dict | None:
    """Returns detailed session info dictionary."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id, user_id, title, active_dir, notes, status, created_at, last_active,
                  (SELECT COUNT(*) FROM messages WHERE session_id LIKE '%' || sessions.id) as msg_count
           FROM sessions WHERE id = ?""",
        (session_id,)
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        if session_id == "general":
            return {
                "id": "general", "user_id": "", "title": "General Scratchpad",
                "active_dir": "", "notes": "General conversation session",
                "status": "active", "created_at": "-", "last_active": "-",
                "msg_count": 0
            }
        return None
    return {
        "id": row[0],
        "user_id": row[1],
        "title": row[2],
        "active_dir": row[3],
        "notes": row[4],
        "status": row[5],
        "created_at": row[6],
        "last_active": row[7],
        "msg_count": row[8]
    }


def update_session(session_id: str, title: str = None, active_dir: str = None, notes: str = None, status: str = None):
    """Updates session fields."""
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    fields = []
    vals = []
    if title is not None:
        fields.append("title = ?")
        vals.append(title)
    if active_dir is not None:
        fields.append("active_dir = ?")
        vals.append(active_dir)
    if notes is not None:
        fields.append("notes = ?")
        vals.append(notes)
    if status is not None:
        fields.append("status = ?")
        vals.append(status)
    fields.append("last_active = ?")
    vals.append(now)
    vals.append(session_id)
    query = f"UPDATE sessions SET {', '.join(fields)} WHERE id = ?"
    cursor.execute(query, tuple(vals))
    conn.commit()
    conn.close()


def get_recent_sessions_summary(user_id: str, exclude_session: str = "general", limit: int = 2) -> str:
    """Returns a compact 2-3 line ambient digest of recent tasks for general chat."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id, title, active_dir, notes, last_active 
           FROM sessions 
           WHERE user_id = ? AND id != ? AND status = 'active'
           ORDER BY last_active DESC LIMIT ?""",
        (str(user_id), exclude_session, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        return ""
    lines = ["[RECENT SESSIONS / ONGOING PROJECTS]:"]
    for r in rows:
        dir_str = f" in `{r[2]}`" if r[2] else ""
        notes_str = f" - {r[3]}" if r[3] else ""
        lines.append(f"• Session `{r[0]}`: {r[1]}{dir_str}{notes_str}")
    return "\n".join(lines)

