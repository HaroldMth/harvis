import subprocess
import sys
import os
import shutil
import logging
import re
import shlex
import signal
import time
import threading

from tools.session import graphical_env

# Tracks running foreground subprocesses so they can be aborted immediately on stop
_ACTIVE_PROCS_LOCK = threading.Lock()
_ACTIVE_PROCS = set()
_BACKGROUND_PROCS = {}  # pid -> {"cmd": clean_cmd, "started_at": float, "log_file": str, "proc": Popen}
_NEW_BACKGROUND_PROCS = []  # list of dicts for newly started background processes


def is_process_alive(pid: int) -> bool:
    """Check if process PID is currently alive and not a zombie."""
    try:
        pid_int = int(pid)
        info = _BACKGROUND_PROCS.get(pid_int)
        if info and info.get("proc"):
            if info["proc"].poll() is not None:
                return False

        status_path = f"/proc/{pid_int}/status"
        if os.path.exists(status_path):
            with open(status_path, "r", errors="ignore") as f:
                for line in f:
                    if line.startswith("State:"):
                        state = line.split()[1]
                        return state != "Z"
            return True

        return subprocess.run(f"kill -0 {pid_int} 2>/dev/null", shell=True).returncode == 0
    except Exception:
        return False


def get_background_proc(pid: int) -> dict | None:
    """Retrieve tracking record for a background process."""
    try:
        return _BACKGROUND_PROCS.get(int(pid))
    except Exception:
        return None


def get_all_background_procs() -> dict:
    """Return all active background processes."""
    return dict(_BACKGROUND_PROCS)


def pop_new_background_procs() -> list[dict]:
    """Returns and clears background processes spawned since last check."""
    global _NEW_BACKGROUND_PROCS
    procs = list(_NEW_BACKGROUND_PROCS)
    _NEW_BACKGROUND_PROCS.clear()
    alive = []
    for p in procs:
        pid = p.get("pid")
        if pid and is_process_alive(pid):
            alive.append(p)
    return alive


def kill_process_by_pid(pid: int) -> bool:
    """Kills a background process by PID and its entire process group."""
    try:
        pid_int = int(pid)
        info = _BACKGROUND_PROCS.pop(pid_int, None)
        proc = info.get("proc") if info else None

        # 1. Kill entire process group
        try:
            pgid = os.getpgid(pid_int)
            os.killpg(pgid, signal.SIGKILL)
        except Exception:
            try:
                os.kill(pid_int, signal.SIGKILL)
            except Exception:
                pass

        if proc:
            try:
                proc.kill()
                proc.wait(timeout=1)
            except Exception:
                pass

        # 2. Kill direct PID as safety fallback
        subprocess.run(f"kill -9 {pid_int} 2>/dev/null", shell=True)
        return True
    except Exception as e:
        logger.warning(f"Failed to kill PID {pid}: {e}")
        return False


def is_continuous_cmd(cmd: str) -> bool:
    """Detects if a command is continuous, a daemon, dev server, watcher, or GUI application."""
    if not cmd or not isinstance(cmd, str):
        return False
    cmd_processed = cmd.strip()

    # 1. Explicit background syntax
    if cmd_processed.endswith("&") or " & " in cmd_processed or cmd_processed.startswith("nohup "):
        return True

    # 2. Known Dev Servers, Daemons, and Watchers
    server_patterns = [
        r"\bnode\s+[\w./-]+\.js\b",
        r"\bnpm\s+(start|run\s+dev|run\s+start)\b",
        r"\bpnpm\s+(start|dev)\b",
        r"\byarn\s+(start|dev)\b",
        r"\bbun\s+(run|start|dev)\b",
        r"\bpython3?\s+.*(app|server|main|serve|manage\.py\s+runserver|bot)\.py\b",
        r"\buvicorn\s+[\w./:]+\b",
        r"\bgunicorn\s+[\w./:]+\b",
        r"\bflask\s+run\b",
        r"\bfastapi\s+dev\b",
        r"\bvite\b",
        r"\bnext\s+(dev|start)\b",
        r"\bnuxt\b",
        r"\bastro\b",
        r"\blive-server\b",
        r"\bhttp-server\b",
        r"\bpython3?\s+-m\s+http\.server\b",
        r"\bcloudflared\s+tunnel\b",
        r"\bngrok\b",
        r"\blt\s+--port\b",
        r"\btail\s+-f\b",
        r"\bjournalctl\s+-f\b",
        r"\bwatch\s+",
    ]
    is_server = any(re.search(pat, cmd_processed) for pat in server_patterns) and not any(
        kw in cmd_processed for kw in ("test", "pytest", "py_compile", "--version", "-v", "help", "--help")
    )
    if is_server:
        return True

    # 3. GUI Applications, Web Launchers, Desktop Tools
    gui_patterns = [
        r"\b(google-chrome(-stable)?|chromium|brave(-browser)?|firefox|zen-browser|epiphany|microsoft-edge|opera|vivaldi)\b",
        r"\b(whatsapp|web\.whatsapp\.com)\b",
        r"\b(code|codium|cursor|subl|kate|kwrite|gedit|mousepad)\b",
        r"\b(dolphin|nautilus|thunar|pcmanfm|nemo)\b",
        r"\b(vlc|mpv|spotify|rhythmbox|audacious)\b",
        r"\b(telegram-desktop|discord|slack|obs|steam|lutris|heroic)\b",
        r"\b(xdg-open|kde-open|gio\s+open)\b",
    ]
    return any(re.search(pat, cmd_processed, re.IGNORECASE) for pat in gui_patterns)


def abort_active_processes():
    """Immediately kill all currently active foreground shell subprocesses."""
    with _ACTIVE_PROCS_LOCK:
        for proc in list(_ACTIVE_PROCS):
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        _ACTIVE_PROCS.clear()

try:
    _repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    from brain.paths import HARVIS_HOME as _HARVIS_HOME
    _HARVIS_HOME_STR = str(_HARVIS_HOME)
except Exception:
    _HARVIS_HOME_STR = os.path.expanduser("~")

# Callback for UI confirmation: function(message: str) -> bool
confirmation_callback = None

# Blocklist: commands that are strictly prohibited (e.g. formatting main drive, etc.)
BLOCKLIST = [
    "rm -rf /",
    "mkfs.ext4 /dev/",
    "dd if=/dev/zero of=/dev/",
]

# Patterns that are never run, however they are spelled (flag order, ~, $HOME, globs...).
BLOCK_PATTERNS = [
    re.compile(r"\brm\s+(-\w+\s+)*-\w*[rRf]\w*\s+(-\w+\s+)*(/|~|\$HOME|/\*|~/\*|\.\./?|\*)(\s|$|/\*)"),
    re.compile(r"--no-preserve-root"),
    re.compile(r"\bmkfs(\.\w+)?\s+/dev/"),
    re.compile(r"\bdd\b[^\n]*\bof=/dev/(sd|nvme|vd|mmcblk|hd)"),
    re.compile(r">\s*/dev/(sd|nvme|vd|mmcblk|hd)"),
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:"),                     # fork bomb
    re.compile(r"\bchmod\s+(-\w+\s+)*(777|a\+rwx)\s+(-\w+\s+)*/(\s|$)"),
    re.compile(r"\bfind\s+(/|~|\$HOME)(\s|$)[^\n]*-delete"),
]

def set_confirmation_callback(cb):
    global confirmation_callback
    confirmation_callback = cb

def request_confirmation(message):
    if confirmation_callback:
        return confirmation_callback(message)
    # Non-interactive daemon mode (systemd service / Telegram bridge): nobody can answer,
    # so default to DENY. Set HARVIS_AUTO_APPROVE=1 to opt in to the old behaviour.
    if not sys.stdin or not sys.stdin.isatty():
        return os.environ.get("HARVIS_AUTO_APPROVE", "") == "1"
    try:
        print(f"\n[CONFIRMATION REQUIRED] {message}")
        ans = input("Allow execution? (y/N): ").strip().lower()
        return ans in ("y", "yes")
    except (EOFError, KeyboardInterrupt, Exception):
        return False

def is_destructive(cmd):
    cmd_lower = cmd.lower()
    destructive_keywords = ["rm ", "rm-", "dd ", "mkfs", "> /dev/", "shutdown", "reboot", "poweroff"]
    for kw in destructive_keywords:
        if kw in cmd_lower:
            return True
    return False

def shell_exec(cmd: str = None, command: str = None, timeout: int = None, is_daemon: bool = False, cwd: str = None, **kwargs):
    cmd = cmd or command or kwargs.get("query") or kwargs.get("script")
    if not cmd:
        return {"success": False, "error": "shell_exec requires a cmd argument."}

    is_daemon = is_daemon or kwargs.get("daemon", False) or kwargs.get("background", False)

    default_timeout = int(os.environ.get("HARVIS_SHELL_TIMEOUT", "120"))
    max_timeout = int(os.environ.get("HARVIS_SHELL_MAX_TIMEOUT", "1800"))
    raw_timeout = timeout or kwargs.get("timeout") or default_timeout
    try:
        wait_time = max(3, min(max_timeout, int(raw_timeout)))
    except Exception:
        wait_time = default_timeout

    # 1. Check against blocklist
    for blocked in BLOCKLIST:
        if blocked in cmd:
            return {"success": False, "error": "Command is blocked due to security policies."}
    for pat in BLOCK_PATTERNS:
        if pat.search(cmd):
            return {"success": False, "error": "Command is blocked due to security policies (matches a destructive pattern)."}
    
    # 2. Check if destructive
    if is_destructive(cmd):
        confirmed = request_confirmation(f"Run destructive shell command: '{cmd}'?")
        if not confirmed:
            return {"success": False, "error": "User denied execution.", "status": "denied"}

    # 3. Run the command exactly as given. Commands that never exit (tail -f,
    # watch, ping, top...) are bounded by the timeout below, which returns a
    # snapshot and flags `timed_out`; the caller sees what actually ran.
    cmd_processed = cmd.strip()

    HARVIS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    run_dir = _HARVIS_HOME_STR  # default cwd is HARVIS's home, not the repo root
    workdir = cwd or kwargs.get("workdir") or kwargs.get("dir")
    if workdir:
        workdir = os.path.abspath(os.path.expanduser(str(workdir)))
        if not os.path.isdir(workdir):
            return {"success": False, "error": f"cwd does not exist or is not a directory: {workdir}"}
        run_dir = workdir

    # 4. Handle Persistent Background Daemons, Servers & GUI Applications
    if is_daemon or is_continuous_cmd(cmd_processed):
        clean_cmd = re.sub(r"\s*&\s*$", "", cmd_processed).strip()
        daemon_id = f"daemon_{int(time.time())}_{os.getpid()}_{len(_BACKGROUND_PROCS) + 1}"
        log_file = f"/tmp/{daemon_id}.log"
        try:
            log_f = open(log_file, "w", encoding="utf-8", errors="replace")
            proc = subprocess.Popen(
                clean_cmd,
                shell=True,
                stdin=subprocess.DEVNULL,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                cwd=run_dir,
                env=graphical_env(),
                start_new_session=True,
                executable="/bin/bash"
            )
            time.sleep(0.5)  # Fast 0.5s check to ensure process started and didn't crash
            ret = proc.poll()
            if ret is not None and ret != 0:
                log_f.close()
                err_text = ""
                if os.path.exists(log_file):
                    try:
                        with open(log_file, "r", errors="ignore") as f:
                            err_text = f.read().strip()[-600:]
                    except Exception:
                        pass
                return {
                    "success": False,
                    "error": f"Command exited immediately with code {ret} (PID {proc.pid}).\nLogs:\n{err_text or '(No output)'}",
                    "log_file": log_file
                }

            pid = proc.pid
            proc_info = {
                "pid": pid,
                "cmd": clean_cmd,
                "started_at": time.time(),
                "log_file": log_file,
                "proc": proc
            }
            _BACKGROUND_PROCS[pid] = proc_info
            _NEW_BACKGROUND_PROCS.append(proc_info)

            initial_logs = ""
            if os.path.exists(log_file):
                try:
                    with open(log_file, "r", errors="ignore") as f:
                        initial_logs = f.read().strip()[-400:]
                except Exception:
                    pass

            return {
                "success": True,
                "output": f"🚀 Started background command (PID: {pid}).\nLogs: {log_file}\n\nInitial output:\n{initial_logs or '(Running in background)'}",
                "pid": pid,
                "log_file": log_file,
                "is_daemon": True,
                "command": clean_cmd
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to start background command: {e}"}

    # 5. Foreground Process Execution with Process Group Isolation & Pipe Decoupling
    try:
        proc = subprocess.Popen(
            cmd_processed,
            shell=True,
            stdin=subprocess.DEVNULL,   # Prevent child processes from holding stdin open
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=run_dir,
            env=graphical_env(),
            start_new_session=True,    # Create a new session so killing pgid cleans all children
            executable="/bin/bash",
        )
        with _ACTIVE_PROCS_LOCK:
            _ACTIVE_PROCS.add(proc)
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
                    "timed_out": True,
                    "output": f"[Command did not exit; killed after {wait_time}s. Output so far]:\n{captured}"
                }
            return {"success": False, "error": f"Command '{cmd}' timed out after {wait_time}s with no output."}
        finally:
            with _ACTIVE_PROCS_LOCK:
                _ACTIVE_PROCS.discard(proc)
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
