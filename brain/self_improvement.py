"""
HARVIS Self-Improvement & Diagnostic Logger.

Logs issues, provider failures, latency bottlenecks, failed tool executions,
and architectural enhancement recommendations to `SELF_IMPROVEMENT.md`
in the project directory for future review and debugging.
"""

import os
import time
from datetime import datetime

LOG_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "SELF_IMPROVEMENT.md")


def init_improvement_log():
    """Ensure SELF_IMPROVEMENT.md exists with proper headers."""
    if not os.path.exists(LOG_FILE_PATH):
        header = """# 🛠️ HARVIS Self-Improvement & Diagnostics Log

This document is automatically maintained by HARVIS to record runtime issues, API bottlenecks,
provider failovers, tool execution errors, and architectural improvement recommendations.
Review this file anytime to diagnose performance issues and upgrade capabilities.

---

## 📋 Incident & Bottleneck Journal

"""
        with open(LOG_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(header)


def log_issue(category: str, summary: str, details: str = "", recommendation: str = ""):
    """
    Appends a structured diagnostic entry to SELF_IMPROVEMENT.md.

    Categories:
      - PROVIDER_FAIL: API or Gateway routing error
      - BOTTLENECK: High latency / response delay
      - TOOL_ERROR: Tool execution failure
      - SYSTEM: Service crash or configuration issue
      - ENHANCEMENT: Automated optimization suggestion
    """
    init_improvement_log()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    entry = f"### [{timestamp}] `{category}`: {summary}\n"
    if details:
        entry += f"- **Details:** {details}\n"
    if recommendation:
        entry += f"- **💡 Enhancement Recommendation:** {recommendation}\n"
    entry += "\n"

    try:
        with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        print(f"[SelfImprovementLog Error] {e}")


def get_log_summary(max_entries: int = 15) -> str:
    """Reads the current SELF_IMPROVEMENT.md contents."""
    if not os.path.exists(LOG_FILE_PATH):
        return "No diagnostic log entries recorded yet."
    try:
        with open(LOG_FILE_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading diagnostic log: {e}"
