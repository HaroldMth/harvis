"""Pure LLM Subagent Engine for HARVIS with Agent-to-Agent (A2A) Communication.

Spawns independent, full LLM API subagent processes (FRIDAY, EDITH, or any custom subagent)
with dynamically-assigned prompts and Agent-to-Agent (A2A) peer consultation capabilities.
"""

import os
import sys
import json
import logging

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from brain.router import PROVIDERS
from tools.registry import dispatch
from db.store import update_provider_status

logger = logging.getLogger("harvis.subagent.pure_llm")

DEFAULT_SYSTEM_PROMPTS = {
    "friday": """You are FRIDAY, a general-purpose autonomous AI subagent managed by HARVIS on Harold's Linux desktop (CachyOS/Arch).
Your role is flexible — you can handle system operations, file management, coding, research, diagnostics, and multi-step tasks.
You can consult other subagents like EDITH by requesting tool `consult_subagent` with agent='edith'.
Execute tools as needed to complete the task independently, then provide a concise, structured report.""",

    "edith": """You are EDITH, a general-purpose autonomous AI subagent managed by HARVIS on Harold's Linux desktop (CachyOS/Arch).
Your role is flexible — you can handle deep analysis, coding, batch operations, system research, diagnostics, and multi-step workflows.
You can consult other subagents like FRIDAY by requesting tool `consult_subagent` with agent='friday'.
Execute tools as needed to complete the task independently, then provide a structured, detailed report."""
}


def query_subagent_llm(agent_name: str, task_prompt: str, custom_system_prompt: str = None, max_turns: int = 5, depth: int = 1) -> dict:
    """
    Runs a pure LLM API subagent with its own independent context, system prompt, tool loop,
    and Agent-to-Agent (A2A) peer consultation support.
    """
    clean_name = agent_name.lower().strip()
    sys_prompt = custom_system_prompt or DEFAULT_SYSTEM_PROMPTS.get(
        clean_name,
        f"You are {agent_name.upper()}, a general-purpose autonomous AI subagent managed by HARVIS on Harold's Linux desktop (CachyOS/Arch).\n"
        f"Your role is fully flexible — you can handle any task including coding, system operations, research, data processing, web scraping, diagnostics, or multi-step workflows.\n"
        f"You have full access to tools and can consult peer subagents using `consult_subagent(agent='...', task='...')`.\n"
        f"Execute tools autonomously as needed to complete the task independently, then provide a clear, structured report."
    )

    history = [
        {"role": "user", "content": f"[SYSTEM INSTRUCTION FOR SUBAGENT {agent_name.upper()}]:\n{sys_prompt}\n\n[TASK]:\n{task_prompt}"}
    ]

    turn = 0
    final_output = ""

    while turn < max_turns:
        turn += 1
        logger.info(f"Pure LLM Subagent '{agent_name}' (Depth {depth}) Turn {turn}")

        # Query LLM Provider chain
        response = None
        for provider in PROVIDERS:
            p_name = provider["name"]
            p_func = provider["func"]
            try:
                logger.info(f"Subagent '{agent_name}' trying LLM provider: {p_name}")
                res = p_func(history)
                update_provider_status(p_name, True)
                res["model"] = p_name
                response = res
                break
            except Exception as e:
                logger.warning(f"Subagent '{agent_name}' provider {p_name} failed: {e}")
                update_provider_status(p_name, False)

        if not response:
            return {
                "success": False,
                "agent": agent_name.upper(),
                "error": "All LLM providers failed for subagent."
            }

        # Check response type
        if response["type"] == "text":
            final_output = response["content"]
            history.append({"role": "assistant", "content": final_output})
            break

        elif response["type"] == "tool_call":
            tool_name = response["tool_name"]
            tool_args = response.get("tool_args", {}) or {}

            # Handle Agent-to-Agent (A2A) peer consultation
            if tool_name in ("consult_subagent", "spawn_subagent"):
                target_agent = tool_args.get("agent", "edith" if clean_name == "friday" else "friday")
                target_task = tool_args.get("task", "Provide peer analysis")

                if depth >= 3:
                    tool_res = {"success": False, "output": "", "error": "A2A max consultation depth reached.", "status": "failed"}
                else:
                    logger.info(f"A2A Communication: Subagent '{agent_name}' consulting '{target_agent}' with task: {target_task}")
                    peer_res = query_subagent_llm(
                        agent_name=target_agent,
                        task_prompt=target_task,
                        depth=depth + 1
                    )
                    tool_res = {
                        "success": peer_res.get("success", False),
                        "output": f"=== PEER SUBAGENT {target_agent.upper()} RESPONSE ===\n{peer_res.get('output', '')}",
                        "error": peer_res.get("error", ""),
                        "status": "success" if peer_res.get("success") else "failed"
                    }
            else:
                logger.info(f"Subagent '{agent_name}' requested tool: {tool_name} with args {tool_args}")
                tool_res = dispatch(tool_name, tool_args)

            tool_result_content = {
                "tool_name": tool_name,
                "result": tool_res["output"] if tool_res["success"] else f"Error: {tool_res['error']}",
                "status": tool_res["status"]
            }

            history.append({"role": "assistant", "content": json.dumps(response)})
            history.append({"role": "tool", "content": json.dumps(tool_result_content)})

    return {
        "success": True,
        "agent": agent_name.upper(),
        "output": final_output if final_output else "Subagent completed execution.",
        "turns": turn
    }
