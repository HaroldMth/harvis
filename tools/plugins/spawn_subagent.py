"""HARVIS Tool Plugin: spawn_subagent

Allows HARVIS to spawn pure LLM subagents (FRIDAY, EDITH, or any dynamically-named agent)
with arbitrary tasks and custom system prompts defined on the fly by HARVIS.
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from subagents.runner import spawn_subagent

PLUGIN_SCHEMA = {
    "name": "spawn_subagent",
    "description": "Spawn an independent, pure LLM AI subagent (e.g. FRIDAY, EDITH, or any custom agent). The subagent executes its own LLM reasoning API call and tools to complete the task defined by HARVIS.",
    "parameters": {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": "Subagent name (e.g. 'friday', 'edith', or any custom subagent name)"
            },
            "task": {
                "type": "string",
                "description": "The exact task prompt for the subagent to reason about and execute"
            },
            "system_prompt": {
                "type": "string",
                "description": "Optional custom system prompt/role definition for the subagent, defined dynamically by HARVIS"
            }
        },
        "required": ["agent", "task"]
    },
    "examples": [
        "spawn_subagent(agent='friday', task='Scan systemd logs for crashes and restart failed units')",
        "spawn_subagent(agent='edith', task='Perform deep stack trace analysis on failing application logs')"
    ]
}


def handler(args):
    if isinstance(args, str):
        task = args
        agent = "friday" if "health" in args.lower() or "journal" in args.lower() else "edith"
        sys_prompt = None
    elif isinstance(args, dict):
        agent = args.get("agent", "friday")
        task = args.get("task", "health audit")
        sys_prompt = args.get("system_prompt")
    else:
        agent = "friday"
        task = "health audit"
        sys_prompt = None

    res = spawn_subagent(agent_name=agent, task=task, system_prompt=sys_prompt)
    if res.get("success"):
        return {"success": True, "output": res.get("output", "")}
    return {"success": False, "error": res.get("error", "Pure LLM Subagent execution failed")}
