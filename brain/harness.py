"""Agent execution harness for HARVIS.

The harness does bookkeeping; the agent makes the decisions.

  * The agent decides when it is done (`finish` tool, or a plain text reply).
    No phrase matching, no step-count shortcuts.
  * The agent sees real history (its own tool calls and their results), not a
    one-observation summary. Older steps roll off a size window but stay in
    <task_dir>/trace.jsonl.
  * The harness reports facts (budget used, repeated actions) as [harness]
    notes. It does not rewrite plans, force rollbacks, or grade the agent's work.
  * Hard stops are limited to: user cancel, the budgets in brain/settings.py,
    and an extreme repeat count.
"""

import json
import time
import uuid
import logging
import asyncio
import inspect
from collections import deque

from brain import settings
from brain.state_manager import TaskManager
from tools.registry import dispatch

logger = logging.getLogger("harvis.harness")

FINISH_TOOL = {
    "name": "finish",
    "description": (
        "End the task. Call this when you are done, or when you decide to stop "
        "for any reason (finished, blocked, or giving up)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "success": {"type": "boolean", "description": "Whether you achieved the goal."},
            "summary": {"type": "string", "description": "What happened, for the user."},
        },
        "required": ["summary"],
    },
}


def _clip(text: str, limit: int) -> str:
    """Keep head and tail; errors and final lines are usually at the end."""
    if not limit or len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    return f"{text[:head]}\n...[{len(text) - limit} chars omitted; full output in trace.jsonl]...\n{text[-tail:]}"


def _content_chars(content) -> int:
    if isinstance(content, list):  # multimodal parts: count text, ignore image payloads
        return sum(len(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") == "text")
    return len(str(content or ""))


def _history_chars(history: list) -> int:
    return sum(_content_chars(m.get("content")) for m in history)


def _default_engine(history, *, system_prompt, mode, stream_callback, extra_tools):
    """One model turn via the router's provider chain (imported lazily)."""
    from brain.router import complete
    return complete(history, system_prompt=system_prompt, mode=mode,
                    stream_callback=stream_callback, extra_tools=extra_tools)


class HarnessRunner:
    def __init__(self, task_id: str = None, mode: str = "normal", engine=None,
                 system_prompt: str = None, history_prefix: list = None):
        """engine: callable(history, *, system_prompt, mode, stream_callback, extra_tools) -> dict | None.
        system_prompt: persona/context placed before the harness prompt.
        history_prefix: earlier chat messages ({"role","content"}) the agent should know about."""
        self.task_id = task_id or f"task_{uuid.uuid4().hex[:8]}"
        self.mode = mode
        self.engine = engine or _default_engine
        self.persona = system_prompt
        self.history_prefix = list(history_prefix or [])
        self.task_mgr = TaskManager(self.task_id)

    def _result(self, status, success, step, **extra):
        out = {
            "task_id": self.task_id,
            "status": status,
            "success": success,
            "step": step,
            "plan": self.task_mgr.read_plan(),
            "trace": self.task_mgr.dir + "/trace.jsonl",
        }
        out.update(extra)
        return out

    async def run(
        self,
        goal,
        progress_callback=None,
        cancel_event=None,
        max_steps: int = None,
        max_duration_sec: int = None,
        stream_callback=None,
    ) -> dict:
        """goal: text, or a list of multimodal message parts (text + images).
        progress_callback may be sync or async."""
        max_steps = settings.MAX_STEPS if max_steps is None else max_steps
        max_duration_sec = settings.MAX_DURATION_SEC if max_duration_sec is None else max_duration_sec

        goal_text = goal if isinstance(goal, str) else " ".join(
            p.get("text", "") for p in goal if isinstance(p, dict) and p.get("type") == "text")
        self.task_mgr.init_task(goal_text, max_steps=max_steps, max_duration_sec=max_duration_sec)
        start = time.time()
        system_prompt = "\n\n".join(p for p in (self.persona, settings.load_prompt()) if p)
        recent = deque(maxlen=settings.REPEAT_WINDOW or 1)

        async def emit(event):
            if progress_callback:
                try:
                    r = progress_callback(event)
                    if inspect.isawaitable(r):
                        await r
                except Exception as e:
                    logger.warning(f"Progress callback error on {event.get('type')}: {e}")

        header = (
            f"[harness] Task directory: {self.task_mgr.dir}\n"
            f"[harness] Full untruncated step trace: {self.task_mgr.dir}/trace.jsonl "
            f"(older steps leave your context window but stay there).\n"
            f"[harness] Budget: "
            f"{max_steps or 'unlimited'} steps, {max_duration_sec or 'unlimited'}s."
        )
        if isinstance(goal, str):
            first = {"role": "user", "content": f"{goal}\n\n{header}"}
        else:
            first = {"role": "user", "content": list(goal) + [{"type": "text", "text": header}]}
        history = self.history_prefix + [first]
        goal_idx = len(self.history_prefix)
        await emit({"type": "init", "task_id": self.task_id, "goal": goal_text, "plan": self.task_mgr.read_plan()})

        step = 0
        while True:
            step += 1
            if cancel_event and cancel_event.is_set():
                return self._result("cancelled", False, step, message="Task cancelled by user.")
            elapsed = time.time() - start
            if max_duration_sec and elapsed > max_duration_sec:
                return self._result("timeout", False, step,
                                    message=f"Exceeded maximum duration of {max_duration_sec}s.")
            if max_steps and step > max_steps:
                return self._result("step_limit_reached", False, step - 1,
                                    message=f"Reached step limit of {max_steps}.",
                                    notes=self.task_mgr.read_notes())

            # Roll the window: drop oldest (assistant call, tool result) pairs, keep the goal.
            while (settings.CONTEXT_CHARS and _history_chars(history) > settings.CONTEXT_CHARS
                   and len(history) > goal_idx + 3):
                del history[goal_idx + 1:goal_idx + 3]

            await emit({"type": "thinking", "step": step})
            try:
                response = await asyncio.to_thread(
                    self.engine, history, system_prompt=system_prompt, mode=self.mode,
                    stream_callback=stream_callback, extra_tools=[FINISH_TOOL],
                )
            except Exception as err:
                if cancel_event and cancel_event.is_set():
                    return self._result("cancelled", False, step, message="Task cancelled by user.")
                logger.warning(f"Engine call failed in harness loop: {err}")
                response = None

            if not response:
                if cancel_event and cancel_event.is_set():
                    return self._result("cancelled", False, step, message="Task cancelled by user.")
                return self._result("provider_error", False, step,
                                    message="All engine providers failed during harness execution.")

            # Plain text, no tool call: the agent chose to stop talking to tools.
            if response.get("type") != "tool_call":
                text = (response.get("content") or "").strip()
                self.task_mgr.append_trace({"step": step, "type": "final_text", "content": text})
                await emit({"type": "completed", "step": step, "summary": text, "plan": self.task_mgr.read_plan()})
                # success is None: the agent didn't declare one and the harness won't invent one.
                return self._result("answered", None, step, output=text)

            tool_name = response.get("tool_name")
            tool_args = response.get("tool_args", {}) or {}

            # The agent decides it is done.
            if tool_name == FINISH_TOOL["name"]:
                summary = str(tool_args.get("summary", "")).strip()
                declared = tool_args.get("success")
                self.task_mgr.append_trace({"step": step, "type": "finish", "success": declared, "summary": summary})
                await emit({"type": "completed", "step": step, "summary": summary, "plan": self.task_mgr.read_plan()})
                return self._result("finished", declared if isinstance(declared, bool) else None,
                                    step, output=summary)

            await emit({"type": "acting", "step": step, "tool_name": tool_name, "tool_args": tool_args,
                  "plan": self.task_mgr.read_plan()})

            t0 = time.time()
            tool_res = await asyncio.to_thread(dispatch, tool_name, tool_args)
            took = round(time.time() - t0, 2)
            ok = bool(tool_res.get("success", False))
            raw = tool_res.get("output", "") or ""
            err = tool_res.get("error", "") or ""
            raw_text = raw if not err else f"{raw}\n[stderr/error]: {err}".strip()

            self.task_mgr.append_trace({
                "step": step, "type": "action", "tool": tool_name, "args": tool_args,
                "success": ok, "seconds": took, "output": raw_text,
            })

            # Facts for the agent. No instructions.
            sig = TaskManager.compute_action_hash(tool_name, tool_args)
            recent.append(sig)
            repeats = sum(1 for s in recent if s == sig)
            facts = [f"step {step}" + (f"/{max_steps}" if max_steps else ""),
                     f"{int(time.time() - start)}s elapsed" + (f"/{max_duration_sec}s" if max_duration_sec else ""),
                     f"success={ok}", f"{took}s"]
            if tool_res.get("timed_out"):
                facts.append("command did not exit on its own and was killed")
            if settings.REPEAT_NOTICE and repeats >= settings.REPEAT_NOTICE:
                facts.append(f"this exact action has run {repeats} times in the last {len(recent)} actions")
            note = "[harness: " + ", ".join(facts) + "]"

            history.append({"role": "assistant",
                            "content": json.dumps({"type": "tool_call", "tool_name": tool_name,
                                                   "tool_args": tool_args})})
            history.append({"role": "tool",
                            "content": f"{_clip(raw_text, settings.OBS_CHARS)}\n{note}"})

            await emit({"type": "checked", "step": step, "tool_name": tool_name, "success": ok,
                  "check_passed": ok, "details": note[:200], "output": raw_text, "plan": self.task_mgr.read_plan()})

            if settings.REPEAT_ABORT and repeats >= settings.REPEAT_ABORT:
                return self._result("stuck", False, step,
                                    message=f"Same action repeated {repeats} times; stopping.",
                                    notes=self.task_mgr.read_notes())
