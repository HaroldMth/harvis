import sqlite3
import os
import json

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "harvis.db"))
SCHEMA_PATH = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "schema.sql"))

def get_connection():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    with open(SCHEMA_PATH, "r") as f:
        schema = f.read()
    cursor.executescript(schema)
    conn.commit()
    conn.close()

BUSY_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "busy_state.json"))


def set_harvis_busy(is_busy: bool):
    """Sets process-level busy state so heartbeat skips while HARVIS handles user requests."""
    try:
        data = {"busy": is_busy, "ts": time.time()}
        with open(BUSY_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def is_harvis_busy() -> bool:
    """Checks if HARVIS is actively processing a user command."""
    if not os.path.exists(BUSY_FILE):
        return False
    try:
        with open(BUSY_FILE, "r") as f:
            data = json.load(f)
        if data.get("busy") and (time.time() - data.get("ts", 0)) < 120:
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

