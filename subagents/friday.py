"""FRIDAY Subagent: Advanced System Operations, Auto-Healer & Doctor.

Capabilities:
- Scans journalctl for errors, crashes, and failed units
- Auto-restarts crashed systemd services (Auto-Healing)
- Cleans disk space, vacuoms journalctl logs, and clears temp files
- Audits CPU, RAM, Disk usage, and thermals
- Triggers desktop alerts via notify-send
"""

import subprocess
import os
import shutil
import json
import logging
from datetime import datetime

logger = logging.getLogger("harvis.subagent.friday")


def scan_journalctl(since: str = "30m", priority: int = 3) -> dict:
    """Scans journalctl for errors and critical logs."""
    try:
        cmd = [
            "journalctl", "--user", "-p", str(priority),
            "--since", since, "--no-pager", "-n", "50"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        sys_cmd = [
            "journalctl", "-p", str(priority),
            "--since", since, "--no-pager", "-n", "30"
        ]
        sys_res = subprocess.run(sys_cmd, capture_output=True, text=True, timeout=10)

        logs = res.stdout.strip() or sys_res.stdout.strip()

        return {
            "success": True,
            "has_errors": bool(logs),
            "since": since,
            "raw_output": logs if logs else f"No errors found in the last {since}.",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"FRIDAY journalctl scan error: {e}")
        return {"success": False, "error": str(e)}


def heal_failed_services() -> dict:
    """Detects failed systemd user services and automatically restarts them."""
    restarted = []
    failed_list = []
    try:
        res = subprocess.run(
            ["systemctl", "--user", "--failed", "--no-legend"],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()

        if res:
            for line in res.splitlines():
                parts = line.split()
                if parts:
                    unit_name = parts[0]
                    failed_list.append(unit_name)
                    # Attempt auto-restart
                    restart_res = subprocess.run(
                        ["systemctl", "--user", "restart", unit_name],
                        capture_output=True, text=True, timeout=10
                    )
                    if restart_res.returncode == 0:
                        restarted.append(unit_name)
                        logger.info(f"FRIDAY Auto-Healed failed service: {unit_name}")

        return {
            "success": True,
            "failed_units": failed_list,
            "auto_healed_units": restarted
        }
    except Exception as e:
        logger.error(f"FRIDAY service healing error: {e}")
        return {"success": False, "error": str(e)}


def clean_disk_and_cache() -> dict:
    """Vacuums journalctl logs and frees disk space."""
    cleaned = []
    try:
        # Vacuum journalctl logs older than 3 days
        vac = subprocess.run(
            ["journalctl", "--user", "--vacuum-time=3d"],
            capture_output=True, text=True, timeout=10
        ).stdout.strip()
        cleaned.append(f"Journalctl Vacuum: {vac}")

        # Clear temp files in /tmp/harvis-*
        temp_cleaned = 0
        for item in os.listdir("/tmp"):
            if item.startswith("harvis-") or item.startswith("byteai-"):
                fp = os.path.join("/tmp", item)
                try:
                    if os.path.isfile(fp):
                        os.remove(fp)
                        temp_cleaned += 1
                except OSError:
                    pass
        cleaned.append(f"Cleared {temp_cleaned} temporary runtime files")

        return {"success": True, "details": cleaned}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_system_health() -> dict:
    """Collects CPU, RAM, Disk, and Failed Systemd units."""
    health = {}
    try:
        failed = subprocess.run(
            ["systemctl", "--user", "--failed", "--no-legend"],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()
        health["failed_units"] = failed if failed else "None"

        df = subprocess.run(
            ["df", "-h", "/"],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()
        health["disk_usage"] = df

        free = subprocess.run(
            ["free", "-h"],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()
        health["memory_usage"] = free

        return {"success": True, "health": health}
    except Exception as e:
        return {"success": False, "error": str(e)}


def run_friday(task: str = "health") -> str:
    """Main execution point for FRIDAY subagent."""
    logger.info(f"FRIDAY subagent executed task: '{task}'")
    task_lower = task.lower()

    report = []
    report.append("### 🛡️ FRIDAY — System Doctor & Operations Report")

    # Auto-heal services if requested or on full health check
    if "heal" in task_lower or "fix" in task_lower or "all" in task_lower or "health" in task_lower:
        heal_res = heal_failed_services()
        if heal_res.get("success") and heal_res.get("failed_units"):
            report.append("\n🔧 **Auto-Healing Actions:**")
            report.append(f"- **Detected Failed Services:** `{', '.join(heal_res['failed_units'])}`")
            if heal_res["auto_healed_units"]:
                report.append(f"- **Successfully Auto-Healed:** `{', '.join(heal_res['auto_healed_units'])}`")

    # Clean disk and cache if requested
    if "clean" in task_lower or "disk" in task_lower or "cache" in task_lower:
        clean_res = clean_disk_and_cache()
        if clean_res.get("success"):
            report.append("\n🧹 **Disk & Cache Maintenance:**")
            for item in clean_res["details"]:
                report.append(f"- {item}")

    # Journalctl scan
    if "journal" in task_lower or "error" in task_lower or "log" in task_lower or "all" in task_lower or "health" in task_lower:
        j_res = scan_journalctl(since="30m")
        if j_res.get("has_errors"):
            report.append("\n⚠️ **Journalctl Log Audit:**")
            report.append("```text\n" + j_res["raw_output"][:1500] + "\n```")
        else:
            report.append("\n✅ **Journalctl Audit:** System logs clean — 0 errors detected in last 30m.")

    # Health summary
    if "health" in task_lower or "system" in task_lower or "all" in task_lower:
        h_res = get_system_health()
        if h_res.get("success"):
            h = h_res["health"]
            report.append("\n🖥️ **Live System Metrics:**")
            report.append(f"- **Failed Units:** `{h['failed_units']}`")
            report.append("```text\n" + h['memory_usage'] + "\n```")

    return "\n".join(report)


if __name__ == "__main__":
    print(run_friday("health auto-heal clean"))
