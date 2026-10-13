"""State on Disk and Lean Working Context Manager for HARVIS.

Maintains autonomous task memory in .harvis/tasks/<task_id>/:
- plan.md: Numbered milestones and verification checks.
- notes.md: Discoveries, environmental facts, hypotheses, and errors.
- state.json: Structured machine state, action signatures, and budget tracking.

Constructs lean working context (< 3,500 tokens) to prevent context bloat and speed decay.
"""

import os
import json
import time
import hashlib
import logging

logger = logging.getLogger("harvis.state_manager")

BASE_TASKS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".harvis", "tasks")


class TaskManager:
    def __init__(self, task_id: str, tasks_dir: str = BASE_TASKS_DIR):
        self.task_id = task_id
        self.dir = os.path.join(tasks_dir, task_id)
        os.makedirs(self.dir, exist_ok=True)
        self.plan_path = os.path.join(self.dir, "plan.md")
        self.notes_path = os.path.join(self.dir, "notes.md")
        self.state_path = os.path.join(self.dir, "state.json")

    def init_task(self, goal: str, max_steps: int = 25, max_duration_sec: int = 600) -> dict:
        """Initializes task state on disk."""
        initial_state = {
            "task_id": self.task_id,
            "goal": goal,
            "status": "running",
            "step_count": 0,
            "max_steps": max_steps,
            "max_duration_sec": max_duration_sec,
            "start_time": time.time(),
            "updated_time": time.time(),
            "action_history": [],  # list of action signature hashes
            "consecutive_failures": 0,
            "stuck_counter": 0,
        }
        self.save_state(initial_state)

        if not os.path.exists(self.plan_path):
            with open(self.plan_path, "w", encoding="utf-8") as f:
                f.write(f"# Goal: {goal}\n")

        if not os.path.exists(self.notes_path):
            with open(self.notes_path, "w", encoding="utf-8") as f:
                f.write(f"# Working Notes for Task: {self.task_id}\n\n")

        return initial_state

    def load_state(self) -> dict:
        """Reads state.json from disk."""
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading state.json: {e}")
        return {}

    def save_state(self, state: dict):
        """Persists state.json to disk."""
        state["updated_time"] = time.time()
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def read_plan(self) -> str:
        """Reads current plan.md."""
        if os.path.exists(self.plan_path):
            with open(self.plan_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        return ""

    def update_plan(self, plan_text: str):
        """Overwrites plan.md with updated milestones."""
        with open(self.plan_path, "w", encoding="utf-8") as f:
            f.write(plan_text.strip() + "\n")

    def append_trace(self, record: dict):
        """Appends one JSON line per step to trace.jsonl (full, untruncated history)."""
        record = dict(record, ts=time.time())
        with open(os.path.join(self.dir, "trace.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def read_notes(self) -> str:
        """Reads notes.md."""
        if os.path.exists(self.notes_path):
            with open(self.notes_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        return ""

    def append_note(self, note: str):
        """Appends discovery or fact to notes.md."""
        with open(self.notes_path, "a", encoding="utf-8") as f:
            f.write(f"- {time.strftime('%H:%M:%S')}: {note.strip()}\n")

    @staticmethod
    def compute_action_hash(tool_name: str, tool_args: dict) -> str:
        """Creates a stable cryptographic hash for an action signature."""
        try:
            norm_args = json.dumps(tool_args or {}, sort_keys=True)
        except Exception:
            norm_args = str(tool_args)
        raw = f"{tool_name}:{norm_args}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def record_step(self, tool_name: str, tool_args: dict, result_success: bool, check_passed: bool) -> dict:
        """Updates step count, action hash history, and failure counters."""
        state = self.load_state()
        state["step_count"] = state.get("step_count", 0) + 1
        
        act_hash = self.compute_action_hash(tool_name, tool_args)
        history = state.get("action_history", [])
        
        # Check duplicate action
        is_duplicate = len(history) > 0 and history[-1] == act_hash
        history.append(act_hash)
        if len(history) > 10:
            history = history[-10:]
        state["action_history"] = history

        # Update failure counters
        if (not result_success) or (not check_passed):
            state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
            if is_duplicate:
                state["stuck_counter"] = state.get("stuck_counter", 0) + 1
        else:
            state["consecutive_failures"] = 0
            state["stuck_counter"] = 0

        self.save_state(state)
        return {
            "step_count": state["step_count"],
            "consecutive_failures": state["consecutive_failures"],
            "stuck_counter": state["stuck_counter"],
            "is_duplicate": is_duplicate,
            "action_hash": act_hash
        }

    def assemble_lean_context(self, system_prompt: str, last_obs: str = "", last_act: dict = None, last_check: dict = None) -> list[dict]:
        """Assembles a compact working context for the model call (< 3,500 tokens)."""
        plan = self.read_plan()
        notes = self.read_notes()
        state = self.load_state()

        # Extract last 10 lines of notes to keep prompt concise
        notes_lines = notes.splitlines()
        notes_snippet = "\n".join(notes_lines[-12:]) if len(notes_lines) > 12 else notes

        user_content = (
            f"### Active Goal: {state.get('goal', 'Execute task')}\n"
            f"Step: {state.get('step_count', 0)} / {state.get('max_steps', 25)}\n\n"
            f"### Current Plan (`plan.md`):\n{plan}\n\n"
            f"### Working Discoveries (`notes.md`):\n{notes_snippet}\n"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

        # Append last observation/action/check as the immediate prior turn if present
        if last_obs or last_act or last_check:
            turn_summary = []
            if last_obs:
                turn_summary.append(f"Observation: {last_obs[:1500]}")
            if last_act:
                turn_summary.append(f"Action: {last_act.get('tool_name')}({last_act.get('tool_args', {})})")
            if last_check:
                status = "PASSED" if last_check.get("passed") else "FAILED"
                turn_summary.append(f"Verification Check: [{status}] {last_check.get('details', '')[:500]}")
            
            messages.append({"role": "assistant", "content": "\n".join(turn_summary)})
            messages.append({
                "role": "user",
                "content": "Evaluate the check result above. Update your plan or notes if needed, then perform the next single action."
            })

        return messages
