import subprocess
import os
import shutil
import logging

# Logger
logger = logging.getLogger("harvis.tools")

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
    else:
        # Fallback to CLI confirmation
        print(f"\n[CONFIRMATION REQUIRED] {message}")
        ans = input("Allow execution? (y/N): ").strip().lower()
        return ans in ("y", "yes")

def is_destructive(cmd):
    cmd_lower = cmd.lower()
    destructive_keywords = ["rm ", "rm-", "dd ", "mkfs", "> /dev/", "shutdown", "reboot", "poweroff"]
    for kw in destructive_keywords:
        if kw in cmd_lower:
            return True
    return False

def shell_exec(cmd: str):
    # 1. Check against blocklist
    for blocked in BLOCKLIST:
        if blocked in cmd:
            return {"success": False, "error": "Command is blocked due to security policies."}
    
    # 2. Check if destructive
    if is_destructive(cmd):
        confirmed = request_confirmation(f"Run destructive shell command: '{cmd}'?")
        if not confirmed:
            return {"success": False, "error": "User denied execution.", "status": "denied"}

    # 3. Execute
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        output = result.stdout
        error = result.stderr
        if result.returncode == 0:
            return {"success": True, "output": output}
        else:
            return {"success": False, "output": output, "error": error, "code": result.returncode}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Command timed out after 30 seconds."}
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
