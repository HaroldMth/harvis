"""EDITH Subagent: Advanced Operator, System Architect & Maintenance Worker.

Capabilities:
- Deep log traceback extraction and stack trace diagnosis
- Checks system package updates, orphans, and locks (CachyOS/Arch)
- Batch file operations and workspace structure audits
- Offloads heavy background operations from HARVIS's main LLM context
"""

import os
import re
import subprocess
import logging
from datetime import datetime

logger = logging.getLogger("harvis.subagent.edith")


def check_system_updates() -> dict:
    """Checks for available Arch/CachyOS system package updates."""
    try:
        res = subprocess.run(
            ["pacman", "-Qu"],
            capture_output=True, text=True, timeout=10
        )
        updates = res.stdout.strip().splitlines() if res.returncode == 0 else []
        return {
            "success": True,
            "update_count": len(updates),
            "pending_updates": updates[:15]
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def deep_log_diagnose(service_name: str = "byteai.service") -> dict:
    """Performs deep log traceback extraction for a specific service or application."""
    try:
        res = subprocess.run(
            ["journalctl", "--user", "-u", service_name, "-n", "100", "--no-pager"],
            capture_output=True, text=True, timeout=10
        )
        logs = res.stdout.strip()
        lines = logs.splitlines()

        tracebacks = []
        for i, line in enumerate(lines):
            if "Traceback" in line or "Error" in line or "Exception" in line or "SIGTRAP" in line:
                start = max(0, i - 2)
                end = min(len(lines), i + 6)
                tracebacks.append("\n".join(lines[start:end]))

        return {
            "success": True,
            "service": service_name,
            "total_log_lines": len(lines),
            "tracebacks_found": len(tracebacks),
            "extracted_tracebacks": tracebacks[:5]
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def directory_audit(directory_path: str = ".") -> dict:
    """Audits directory sizes, file counts, and extension distribution."""
    if not os.path.exists(directory_path):
        return {"success": False, "error": f"Directory not found: {directory_path}"}

    try:
        stats = {"total_files": 0, "total_size_mb": 0, "extensions": {}}
        for root, _, files in os.walk(directory_path):
            for file in files:
                stats["total_files"] += 1
                fp = os.path.join(root, file)
                try:
                    size = os.path.getsize(fp)
                    stats["total_size_mb"] += size / (1024 * 1024)
                except OSError:
                    pass
                ext = os.path.splitext(file)[1].lower() or "no_ext"
                stats["extensions"][ext] = stats["extensions"].get(ext, 0) + 1

        stats["total_size_mb"] = round(stats["total_size_mb"], 2)
        return {"success": True, "stats": stats}
    except Exception as e:
        return {"success": False, "error": str(e)}


def run_edith(task: str = "analyze") -> str:
    """Main execution point for EDITH subagent."""
    logger.info(f"EDITH subagent executed task: '{task}'")
    task_lower = task.lower()

    report = []
    report.append("### ⚙️ EDITH — System Operator & Deep Maintenance Report")

    # Check system package updates
    if "update" in task_lower or "pacman" in task_lower or "package" in task_lower or "maintenance" in task_lower:
        u_res = check_system_updates()
        if u_res.get("success"):
            cnt = u_res["update_count"]
            report.append(f"\n📦 **System Package Update Status:**")
            report.append(f"- **Pending Updates:** `{cnt}` packages available")
            if u_res["pending_updates"]:
                report.append("```text\n" + "\n".join(u_res["pending_updates"]) + "\n```")

    # Service Deep Diagnostics
    if "diagnose" in task_lower or "traceback" in task_lower or "crash" in task_lower or "service" in task_lower:
        service_target = "byteai.service"
        for word in task.split():
            if ".service" in word:
                service_target = word
        diag_res = deep_log_diagnose(service_target)
        if diag_res.get("success"):
            report.append(f"\n🔍 **Deep Diagnostics for `{service_target}`:**")
            report.append(f"- **Tracebacks Found:** `{diag_res['tracebacks_found']}`")
            if diag_res["extracted_tracebacks"]:
                report.append("```python\n" + "\n---\n".join(diag_res["extracted_tracebacks"]) + "\n```")

    # Directory audit fallback
    if "audit" in task_lower or "file" in task_lower or "workspace" in task_lower or len(report) == 1:
        res = directory_audit(".")
        if res.get("success"):
            s = res["stats"]
            report.append(f"\n📁 **Workspace Audit (`/home/harold/HARVIS`):**")
            report.append(f"- **Total Files:** `{s['total_files']}` | **Size:** `{s['total_size_mb']} MB`")

    return "\n".join(report)


if __name__ == "__main__":
    print(run_edith("diagnose byteai.service and update check"))
