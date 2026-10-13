"""HARVIS Tool Plugin: self_improvement_log

Allows HARVIS to read or append manually to his internal SELF_IMPROVEMENT.md journal.
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from brain.self_improvement import get_log_summary, log_issue

PLUGIN_SCHEMA = {
    "name": "self_improvement_log",
    "description": "Read or write entries to HARVIS's personal SELF_IMPROVEMENT.md diagnostic journal. Use action='read' to review past issues, bottlenecks, and recommendations, or action='log' to record a new issue/enhancement.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "log"],
                "description": "Action to perform: 'read' (read the journal) or 'log' (record a new entry)."
            },
            "category": {
                "type": "string",
                "description": "Category for logging (e.g. PROVIDER_FAIL, BOTTLENECK, TOOL_ERROR, SYSTEM, ENHANCEMENT)."
            },
            "summary": {
                "type": "string",
                "description": "Short summary of the issue or enhancement."
            },
            "details": {
                "type": "string",
                "description": "Detailed explanation of what occurred."
            },
            "recommendation": {
                "type": "string",
                "description": "Proposed fix, optimization, or architectural recommendation."
            }
        },
        "required": ["action"]
    }
}


def handler(args):
    action = args.get("action", "read")
    if action == "read":
        log_content = get_log_summary()
        return {"success": True, "output": log_content}
    elif action == "log":
        cat = args.get("category", "ENHANCEMENT")
        summary = args.get("summary", "Manual log entry")
        details = args.get("details", "")
        rec = args.get("recommendation", "")
        log_issue(cat, summary, details, rec)
        return {"success": True, "output": f"Logged issue to SELF_IMPROVEMENT.md: [{cat}] {summary}"}
    return {"success": False, "error": f"Unknown action '{action}'"}
