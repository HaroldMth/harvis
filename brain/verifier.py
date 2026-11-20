"""Automated Real-World Verification Gatekeeper for HARVIS.

Never trust model self-reporting ('I fixed it'). A task or step is only complete
when an automated external check passes (exit code 0, regex match, file presence,
or service active status).
"""

import os
import re
import subprocess
import logging

logger = logging.getLogger("harvis.verifier")


def verify_command(cmd: str, timeout: int = 30) -> dict:
    """Executes a bash verification command and checks for exit code 0."""
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            executable="/bin/bash"
        )
        passed = (proc.returncode == 0)
        output_snippet = (proc.stdout or proc.stderr or "").strip()
        return {
            "passed": passed,
            "exit_code": proc.returncode,
            "output": output_snippet[:1000],
            "details": f"Exit code: {proc.returncode}. Output: {output_snippet[:300]}"
        }
    except subprocess.TimeoutExpired:
        return {
            "passed": False,
            "exit_code": -1,
            "output": "Command timed out",
            "details": f"Verification command '{cmd}' timed out after {timeout}s."
        }
    except Exception as e:
        return {
            "passed": False,
            "exit_code": -1,
            "output": str(e),
            "details": f"Verification execution error: {e}"
        }


def verify_file(path: str, min_bytes: int = 1, pattern: str = None) -> dict:
    """Checks if a file exists, has minimum size, and optionally matches a regex."""
    abs_path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(abs_path):
        return {
            "passed": False,
            "details": f"File does not exist: {abs_path}"
        }
    
    size = os.path.getsize(abs_path)
    if size < min_bytes:
        return {
            "passed": False,
            "details": f"File {abs_path} is too small ({size} bytes < {min_bytes} bytes requirement)."
        }
        
    if pattern:
        try:
            with open(abs_path, "r", errors="ignore") as f:
                content = f.read()
            if not re.search(pattern, content, re.MULTILINE):
                return {
                    "passed": False,
                    "details": f"File {abs_path} does not match required regex pattern: {pattern}"
                }
        except Exception as e:
            return {
                "passed": False,
                "details": f"Failed to read file {abs_path} for regex check: {e}"
            }

    return {
        "passed": True,
        "details": f"File {abs_path} verified (size: {size} bytes)."
    }


def verify_service(service_name: str) -> dict:
    """Checks if a systemd service unit is active."""
    clean_name = service_name.strip()
    if not clean_name.endswith(".service"):
        clean_name += ".service"
    return verify_command(f"systemctl is-active {clean_name}")


def run_verification(check_spec: str | dict) -> dict:
    """Dispatches a generic verification check specification.
    
    Supports:
    - "cmd:<bash command>" or raw bash command
    - "file:<filepath> [regex:<pattern>]"
    - "service:<service_name>"
    - Dict format: {"type": "cmd"|"file"|"service", ...}
    """
    if not check_spec:
        return {"passed": True, "details": "No verification required."}

    if isinstance(check_spec, dict):
        ctype = check_spec.get("type", "cmd")
        if ctype == "file":
            return verify_file(
                check_spec.get("path", ""),
                min_bytes=check_spec.get("min_bytes", 1),
                pattern=check_spec.get("pattern")
            )
        elif ctype == "service":
            return verify_service(check_spec.get("name", ""))
        else:
            return verify_command(check_spec.get("cmd", check_spec.get("command", "")))

    spec = str(check_spec).strip()
    if spec.startswith("service:"):
        sname = spec.split("service:", 1)[1].strip()
        return verify_service(sname)
    elif spec.startswith("file:"):
        fpath = spec.split("file:", 1)[1].strip()
        pattern = None
        if " regex:" in fpath:
            fpath, pattern = fpath.split(" regex:", 1)
            pattern = pattern.strip()
        return verify_file(fpath.strip(), pattern=pattern)
    elif spec.startswith("cmd:"):
        cmd = spec.split("cmd:", 1)[1].strip()
        return verify_command(cmd)
    else:
        # Default to treating as a command
        return verify_command(spec)
