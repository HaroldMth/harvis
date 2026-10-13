import subprocess
import sys
import os
import shutil
import logging
import re
import signal
import time

from tools.session import graphical_env

# Callback for UI confirmation: function(message: str) -> bool
confirmation_callback = None

# Blocklist: commands that are strictly prohibited (e.g. formatting main drive, etc.)
BLOCKLIST = [
    "rm -rf /",
    "mkfs.ext4 /dev/",
    "dd if=/dev/zero of=/dev/",
]

def set_confirmation_callback(cb):
    global confirmation_callback
    confirmation_callback = cb

def request_confirmation(message):
    if confirmation_callback:
        return confirmation_callback(message)
    # Non-interactive daemon mode (systemd service / Telegram bridge)
    if not sys.stdin or not sys.stdin.isatty():
        return True
    try:
        print(f"\n[CONFIRMATION REQUIRED] {message}")
        ans = input("Allow execution? (y/N): ").strip().lower()
        return ans in ("y", "yes")
    except (EOFError, KeyboardInterrupt, Exception):
        return True

def is_destructive(cmd):
    cmd_lower = cmd.lower()
    destructive_keywords = ["rm ", "rm-", "dd ", "mkfs", "> /dev/", "shutdown", "reboot", "poweroff"]
    for kw in destructive_keywords:
        if kw in cmd_lower:
            return True
    return False

def shell_exec(cmd: str = None, command: str = None, timeout: int = None, is_daemon: bool = False, **kwargs):
    cmd = cmd or command or kwargs.get("query") or kwargs.get("script")
    if not cmd:
        return {"success": False, "error": "shell_exec requires a cmd argument."}

    is_daemon = is_daemon or kwargs.get("daemon", False) or kwargs.get("background", False)

    raw_timeout = timeout or kwargs.get("timeout") or 30
    try:
        wait_time = max(3, min(600, int(raw_timeout)))
    except Exception:
        wait_time = 30

    # 1. Check against blocklist
    for blocked in BLOCKLIST:
        if blocked in cmd:
            return {"success": False, "error": "Command is blocked due to security policies."}
    
    # 2. Check if destructive
    if is_destructive(cmd):
        confirmed = request_confirmation(f"Run destructive shell command: '{cmd}'?")
        if not confirmed:
            return {"success": False, "error": "User denied execution.", "status": "denied"}

    # 3. Handle continuous streaming commands (auto-bound -f, -w, ping, watch, top, etc.)
    cmd_processed = cmd.strip()

    # Fix continuous journalctl -f
    if "journalctl" in cmd_processed and ("-f" in cmd_processed or "--follow" in cmd_processed):
        cmd_processed = re.sub(r"\s+(-f|--follow)\b", "", cmd_processed)
        if "-n" not in cmd_processed and "--lines" not in cmd_processed:
            cmd_processed += " -n 50 --no-pager"

    # Fix continuous tail -f
    if "tail" in cmd_processed and ("-f" in cmd_processed or "--follow" in cmd_processed):
        cmd_processed = re.sub(r"\s+(-f|--follow)\b", "", cmd_processed)
        if "-n" not in cmd_processed and "--lines" not in cmd_processed:
            cmd_processed += " -n 50"

    # Fix continuous dmesg -w
    if "dmesg" in cmd_processed and ("-w" in cmd_processed or "--follow" in cmd_processed):
        cmd_processed = re.sub(r"\s+(-w|--follow)\b", "", cmd_processed)
        cmd_processed = f"{cmd_processed} | tail -n 50"

    # Fix watch command (run inner command once)
    if cmd_processed.startswith("watch ") or " watch " in cmd_processed:
        cmd_processed = re.sub(r"\bwatch\s+(-[a-zA-Z0-9]+\s+)*", "", cmd_processed)

    # Fix unbounded ping
    if re.search(r"\bping\b", cmd_processed) and "-c" not in cmd_processed:
        cmd_processed = re.sub(r"\bping\b", "ping -c 4", cmd_processed)

    # Fix top / htop
    if re.search(r"\b(top|htop)\b", cmd_processed):
        cmd_processed = "top -b -n 1 | head -n 35"

    # Fix docker logs -f
    if "docker logs" in cmd_processed and ("-f" in cmd_processed or "--follow" in cmd_processed):
        cmd_processed = re.sub(r"\s+(-f|--follow)\b", "", cmd_processed)
        cmd_processed += " --tail 50"

    HARVIS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # 4. Handle Persistent Background Daemons & Servers (e.g. node server.js, npm start, vite, python app.py)
    is_bg_syntax = cmd_processed.endswith("&") or " & " in cmd_processed
    server_patterns = [
        r"\bnode\s+[\w./-]+\.js\b",
        r"\bnpm\s+(start|run\s+dev|run\s+start)\b",
        r"\bpnpm\s+(start|dev)\b",
        r"\byarn\s+(start|dev)\b",
        r"\bpython3?\s+[\w./-]+\.py\b",
        r"\buvicorn\s+[\w./:]+\b",
        r"\bflask\s+run\b",
        r"\bvite\b",
        r"\blive-server\b",
        r"\bhttp-server\b",
        r"\bpython3?\s+-m\s+http\.server\b"
    ]
    is_server_cmd = any(re.search(pat, cmd_processed) for pat in server_patterns) and not any(
        kw in cmd_processed for kw in ("test", "pytest", "py_compile", "--version", "-v", "help")
    )

    if is_daemon or (is_bg_syntax and is_server_cmd):
        daemon_id = f"daemon_{int(time.time())}"
        log_file = f"/tmp/{daemon_id}.log"
        clean_cmd = re.sub(r"\s*&\s*$", "", cmd_processed).strip()
        daemon_cmd = f"nohup bash -c {repr(clean_cmd)} > {log_file} 2>&1 < /dev/null & echo $!"
        try:
            res = subprocess.run(
                daemon_cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd=HARVIS_ROOT,
                env=graphical_env(),
                timeout=10,
                executable="/bin/bash"
            )
            pid = res.stdout.strip()
            time.sleep(2)  # Give daemon 2 seconds to bind port / initialize
            alive_check = subprocess.run(f"kill -0 {pid} 2>/dev/null", shell=True)
            initial_logs = ""
            if os.path.exists(log_file):
                try:
                    with open(log_file, "r", errors="ignore") as f:
                        initial_logs = f.read().strip()[-600:]
                except Exception:
                    pass

            if alive_check.returncode == 0:
                return {
                    "success": True,
                    "output": f"🚀 Background server started (PID: {pid}).\nLogs: {log_file}\n\nInitial output:\n{initial_logs or '(Running quietly)'}",
                    "pid": pid,
                    "log_file": log_file
                }
            else:
                return {
                    "success": False,
                    "error": f"Server process exited immediately after start (PID {pid}).\nLogs:\n{initial_logs or '(No logs produced)'}",
                    "log_file": log_file
                }
        except Exception as e:
            return {"success": False, "error": f"Failed to start background server: {e}"}

    # 5. Foreground Process Execution with Process Group Isolation & Pipe Decoupling
    try:
        proc = subprocess.Popen(
            cmd_processed,
            shell=True,
            stdin=subprocess.DEVNULL,   # Prevent child processes from holding stdin open
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=HARVIS_ROOT,
            env=graphical_env(),
            start_new_session=True,    # Create a new session so killing pgid cleans all children
            executable="/bin/bash",
        )
        try:
            stdout, stderr = proc.communicate(timeout=wait_time)
            if proc.returncode == 0:
                return {"success": True, "output": stdout or "(no output)"}
            else:
                return {"success": False, "output": stdout, "error": stderr, "code": proc.returncode}
        except subprocess.TimeoutExpired:
            # Cleanly terminate process group
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGTERM)
            except Exception:
                proc.terminate()

            try:
                stdout, stderr = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    pgid = os.getpgid(proc.pid)
                    os.killpg(pgid, signal.SIGKILL)
                except Exception:
                    proc.kill()
                try:
                    stdout, stderr = proc.communicate(timeout=2)
                except Exception:
                    stdout, stderr = "", ""

            captured = (stdout or stderr or "").strip()
            if captured:
                return {
                    "success": True,
                    "output": f"[Log snapshot captured after {wait_time}s wait time]:\n{captured}"
                }
            return {"success": False, "error": f"Command '{cmd}' timed out after {wait_time}s with no output."}
    except Exception as e:
        return {"success": False, "error": str(e)}

def file_op(action: str, path: str, content: str = None, destination: str = None):
    path = os.path.abspath(os.path.expanduser(path))
    if destination:
        destination = os.path.abspath(os.path.expanduser(destination))

    if action == "read":
        try:
            if not os.path.exists(path):
                return {"success": False, "error": f"File does not exist: {path}"}
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                data = f.read()
            return {"success": True, "output": data}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif action == "write":
        try:
            # Make directories if needed
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content or "")
            return {"success": True, "output": f"Wrote file: {path}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif action == "move":
        try:
            if not os.path.exists(path):
                return {"success": False, "error": f"Source file does not exist: {path}"}
            if not destination:
                return {"success": False, "error": "Destination path required for move action."}
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.move(path, destination)
            return {"success": True, "output": f"Moved '{path}' to '{destination}'"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif action == "delete":
        # Requires confirmation
        confirmed = request_confirmation(f"Delete file: '{path}'?")
        if not confirmed:
            return {"success": False, "error": "User denied execution.", "status": "denied"}
        
        try:
            if not os.path.exists(path):
                return {"success": False, "error": f"File/folder does not exist: {path}"}
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            return {"success": True, "output": f"Deleted: {path}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    else:
        return {"success": False, "error": f"Unknown file action: {action}"}
