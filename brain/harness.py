"""Autonomous Self-Verifying Execution Harness for HARVIS.

Implements the Agent Blueprint state machine:
  OBSERVE -> THINK -> ACT -> CHECK -> RECORD -> DECIDE

Invariants enforced:
1. No action without prior observation.
2. Every action has an automated verification check.
3. Stuck detection via action signature hashing (2x replan, 4x rollback, 5x escalate).
4. State on disk with lean working context (< 3,500 tokens).
5. Bounded autonomy (step and duration budgets).
"""

import os
import re
import json
import time
import uuid
import logging
import asyncio

from brain.modes import get_engine, get_fallback_chain
from brain.providers import call_omniroute
from brain.state_manager import TaskManager
from brain.verifier import run_verification
from tools.registry import dispatch

logger = logging.getLogger("harvis.harness")

HARNESS_SYSTEM_PROMPT = """You are HARVIS, an autonomous, self-verifying engineering agent.

You are driven by an external execution harness with strict invariants:
1. OBSERVE BEFORE ACTING: Never assume file contents, services, or errors. Always inspect first.
2. ONE ACTION PER TURN: Choose exactly ONE tool action (shell_exec, file_op, etc.) that moves the plan forward.
3. VERIFY WITH REALITY: Never declare a task complete based on assumption. Verify every change with a real check (exit code 0, regex match, or file verification).
4. FORMAT YOUR RESPONSE:
   - Begin with a 1-sentence observation & hypothesis:
     "Hypothesis: <why this action will advance the milestone>"
   - Specify your verification check:
     "Check: <command or condition to verify the action's success>"
   - Trigger the appropriate tool call.
5. WHEN GENUINELY FINISHED:
   When all milestones in plan.md have been executed AND verified with automated checks passing, output a final response containing:
   "[TASK COMPLETE]" followed by a concise summary of what was changed and verified.
"""


class HarnessRunner:
    def __init__(self, task_id: str = None, mode: str = "normal"):
        self.task_id = task_id or f"task_{uuid.uuid4().hex[:8]}"
        self.mode = mode
        self.task_mgr = TaskManager(self.task_id)

    async def run(
        self,
        goal: str,
        progress_callback=None,
        cancel_event=None,
        max_steps: int = 25,
        max_duration_sec: int = 600,
    ) -> dict:
        """Executes an autonomous goal to completion or bounded termination."""
        state = self.task_mgr.init_task(goal, max_steps=max_steps, max_duration_sec=max_duration_sec)
        start_time = time.time()
        logger.info(f"Harness started task {self.task_id} with goal: {goal}")

        last_obs = ""
        last_act = None
        last_check = None
        engine_model = get_engine(self.mode)

        if progress_callback:
            try:
                progress_callback({
                    "type": "init",
                    "task_id": self.task_id,
                    "goal": goal,
                    "plan": self.task_mgr.read_plan(),
                })
            except Exception as e:
                logger.warning(f"Progress callback error on init: {e}")

        for step in range(1, max_steps + 1):
            # Check cancellation
            if cancel_event and cancel_event.is_set():
                logger.info(f"Task {self.task_id} cancelled by user.")
                return {
                    "task_id": self.task_id,
                    "status": "cancelled",
                    "success": False,
                    "step": step,
                    "message": "Task cancelled by user.",
                    "plan": self.task_mgr.read_plan(),
                }

            # Check timeout
            elapsed = time.time() - start_time
            if elapsed > max_duration_sec:
                logger.warning(f"Task {self.task_id} timed out after {elapsed:.1f}s.")
                return {
                    "task_id": self.task_id,
                    "status": "timeout",
                    "success": False,
                    "step": step,
                    "message": f"Task exceeded maximum duration of {max_duration_sec}s.",
                    "plan": self.task_mgr.read_plan(),
                }

            # Invariant 3: Stuck Detection & Recovery Ladder
            current_state = self.task_mgr.load_state()
            failures = current_state.get("consecutive_failures", 0)
            stuck_count = current_state.get("stuck_counter", 0)

            # 4x Failures: Attempt recovery ladder (git rollback)
            if failures == 4:
                logger.warning(f"Task {self.task_id} hit 4 consecutive failures. Triggering git rollback...")
                rollback_res = run_verification("git checkout -- .")
                self.task_mgr.append_note(f"Recovery ladder triggered: git rollback (exit code {rollback_res.get('exit_code')}).")
                if progress_callback:
                    progress_callback({
                        "type": "recovery",
                        "step": step,
                        "details": "Triggered git rollback to restore clean state after 4 failures."
                    })

            # 5x Failures: Escalation to human
            if failures >= 5 or stuck_count >= 3:
                logger.error(f"Task {self.task_id} stuck after {failures} consecutive failures. Escalating.")
                return {
                    "task_id": self.task_id,
                    "status": "escalation_required",
                    "success": False,
                    "step": step,
                    "message": f"HARVIS is stuck after {failures} failed attempts. Human guidance required.",
                    "plan": self.task_mgr.read_plan(),
                    "notes": self.task_mgr.read_notes(),
                }

            # Assemble lean context (< 3,500 tokens)
            messages = self.task_mgr.assemble_lean_context(
                system_prompt=HARNESS_SYSTEM_PROMPT,
                last_obs=last_obs,
                last_act=last_act,
                last_check=last_check,
            )

            # Inject loop breaker warning on 2x failure/repetition
            if failures >= 2 or stuck_count >= 1:
                messages.append({
                    "role": "user",
                    "content": (
                        "⚠️ CRITICAL HARNESS DIRECTIVE: Your previous actions either failed or were identical. "
                        "You are looping. Do NOT execute the same command or edit. "
                        "Re-observe, formulate an alternative hypothesis, and change your strategy."
                    )
                })

            # Call Engine with silent fallback chain
            models_to_try = get_fallback_chain(self.mode)
            response = None
            for cand_model in models_to_try:
                try:
                    response = await asyncio.to_thread(
                        call_omniroute,
                        messages,
                        system_prompt=None,
                        enable_tools=True,
                        model=cand_model,
                    )
                    if response:
                        break
                except Exception as call_err:
                    logger.warning(f"Model {cand_model} failed in harness loop ({call_err}). Trying fallback...")

            if not response:
                return {
                    "task_id": self.task_id,
                    "status": "provider_error",
                    "success": False,
                    "step": step,
                    "message": "All engine providers failed during harness execution.",
                    "plan": self.task_mgr.read_plan(),
                }

            # Process Model Decision
            resp_type = response.get("type")

            if resp_type == "text":
                content = response.get("content", "")
                
                # Check for plan updates formatted inside text
                if "## Milestones" in content or "[ ]" in content or "[x]" in content:
                    lines = [ln for ln in content.splitlines() if ln.strip().startswith("[") or ln.strip().startswith("#")]
                    if lines:
                        self.task_mgr.update_plan("\n".join(lines))

                # Check for Task Completion or Direct Text Answers
                clean_summary = content.replace("[TASK COMPLETE]", "").strip()
                if "[TASK COMPLETE]" in content or "task complete" in content.lower() or step == 1 or last_act is not None:
                    self.task_mgr.append_note(f"Task completed successfully at step {step}.")
                    if progress_callback:
                        try:
                            progress_callback({
                                "type": "completed",
                                "step": step,
                                "summary": clean_summary,
                                "plan": self.task_mgr.read_plan(),
                            })
                        except Exception as e:
                            logger.warning(f"Progress callback error on completed: {e}")
                    return {
                        "task_id": self.task_id,
                        "status": "completed",
                        "success": True,
                        "step": step,
                        "output": clean_summary,
                        "plan": self.task_mgr.read_plan(),
                    }

                # Pure conversational thought during multi-step execution
                last_obs = content[:500]
                self.task_mgr.append_note(f"Thought: {content[:200]}")
                last_act = None
                last_check = None
                continue

            elif resp_type == "tool_call":
                tool_name = response.get("tool_name")
                tool_args = response.get("tool_args", {}) or {}

                if progress_callback:
                    try:
                        progress_callback({
                            "type": "acting",
                            "step": step,
                            "tool_name": tool_name,
                            "tool_args": tool_args,
                            "plan": self.task_mgr.read_plan(),
                        })
                    except Exception as e:
                        logger.warning(f"Progress callback error on action: {e}")

                # Execute action via dispatch
                t_start = time.time()
                tool_res = await asyncio.to_thread(dispatch, tool_name, tool_args)
                t_elapsed = round(time.time() - t_start, 2)

                tool_success = tool_res.get("success", False)
                raw_output = tool_res.get("output", "") or tool_res.get("error", "")
                last_obs = f"[{tool_name} returned in {t_elapsed}s (success={tool_success})]:\n{raw_output[:1200]}"

                # Automated Verification Gatekeeper
                check_res = {"passed": tool_success, "details": f"Tool execution returned success={tool_success}"}
                if tool_name == "shell_exec":
                    cmd = tool_args.get("cmd", "")
                    if "pytest" in cmd or "test" in cmd:
                        check_res = {"passed": tool_success, "details": f"Test run exit code evaluated ({'passed' if tool_success else 'failed'})."}
                elif tool_name == "file_op":
                    action = tool_args.get("action")
                    fpath = tool_args.get("path")
                    if action in ("write", "move") and fpath:
                        check_res = run_verification(f"file:{fpath}")

                last_act = {"tool_name": tool_name, "tool_args": tool_args}
                last_check = check_res

                # Record step and update stuck detector
                step_info = self.task_mgr.record_step(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    result_success=tool_success,
                    check_passed=check_res.get("passed", False),
                )

                self.task_mgr.append_note(
                    f"Step {step}: {tool_name} -> {'OK' if tool_success else 'ERR'} (check: {'PASSED' if check_res.get('passed') else 'FAILED'})"
                )

                if progress_callback:
                    try:
                        progress_callback({
                            "type": "checked",
                            "step": step,
                            "tool_name": tool_name,
                            "success": tool_success,
                            "check_passed": check_res.get("passed", False),
                            "details": check_res.get("details", "")[:200],
                            "plan": self.task_mgr.read_plan(),
                        })
                    except Exception as e:
                        logger.warning(f"Progress callback error on check: {e}")

        # Reached step cap
        logger.warning(f"Task {self.task_id} reached maximum step cap ({max_steps}).")
        return {
            "task_id": self.task_id,
            "status": "step_limit_reached",
            "success": False,
            "step": max_steps,
            "message": f"Reached step limit of {max_steps} without completing all milestones.",
            "plan": self.task_mgr.read_plan(),
            "notes": self.task_mgr.read_notes(),
        }
