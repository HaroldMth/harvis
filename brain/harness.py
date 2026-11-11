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
import hashlib
import os
import re
import subprocess
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

UPDATE_TASK_TOOL = {
    "name": "update_task",
    "description": (
        "Rewrite your own task file (task.md). Call it at the start with a checklist "
        "('- [ ] step'), and again whenever steps finish, plans change, or you learn "
        "something worth keeping (decisions, files changed, what is left). The current "
        "file is shown to you on every turn and survives context trimming and restarts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "Full markdown contents of task.md."},
        },
        "required": ["content"],
    },
}


def _task_block(task_mgr) -> str:
    """Current task file, injected into the system prompt on every turn."""
    plan = task_mgr.read_plan()
    # A bare "# Goal: ..." header means the agent hasn't written a plan yet.
    if not plan or len([l for l in plan.splitlines() if l.strip()]) <= 1:
        return ("[task file] task.md is empty. Call update_task now with a checklist "
                "for this goal if the work takes more than a couple of steps.")
    # Capped: this block is re-sent on every model call, so an ever-growing plan would bring the
    # prompt-growth problem straight back. The full file stays on disk.
    return f"[task file: task.md - your own plan and notes, rewrite it with update_task]\n{_clip_mid(plan, 6000)}"


def _result_sig(tool_name, tool_args, raw_text) -> str:
    """Same action AND same output. Re-running tests after an edit changes the output,
    so a normal edit/test loop is not counted as repetition."""
    base = TaskManager.compute_action_hash(tool_name, tool_args)
    return base + ":" + hashlib.md5((raw_text or "").encode("utf-8", "ignore")).hexdigest()[:12]


def _call_engine(engine, *args, **kwargs):
    """Run the model call in a worker thread. StopIteration must not escape: raised inside an asyncio
    thread future it is swallowed by the machinery and the whole task hangs forever."""
    try:
        return engine(*args, **kwargs)
    except StopIteration as e:
        raise RuntimeError("engine raised StopIteration (exhausted generator?)") from e


_PROMISE_PHRASES = (
    "i'm on it", "i am on it", "building now", "actively being built", "started implementing",
    "i will build", "i will create", "let me code", "let's build", "i'm starting to build",
    "i have started", "i've started",
)

# --- Batching, snapshots and verification --------------------------------------------------
READ_ONLY_TOOLS = {"read_file", "grep", "glob_files", "list_dir", "web_search", "fetch_url",
                   "system_info", "browser_fetch"}
_READONLY_SHELL = re.compile(
    r"^\s*(ls|cat|head|tail|grep|rg|pwd|echo|wc|which|stat|file|du|df|tree|sed\s+-n|"
    r"git\s+(status|diff|log|show|branch)|find\b(?!.*-(delete|exec)))", re.I)
_VERIFY_SHELL = re.compile(
    r"\b(pytest|unittest|npm\s+(run\s+)?(test|build|lint)|yarn\s+(test|build)|pnpm\s+(test|build)|"
    r"cargo\s+(test|build|check)|go\s+(test|build|vet)|make(\s+(test|check|build))?|tsc|mypy|ruff|flake8|"
    r"py_compile|eslint|jest|vitest)\b", re.I)


def _is_verify(name, args) -> bool:
    if name == "run_tests":
        return True
    return name == "shell_exec" and bool(_VERIFY_SHELL.search(str((args or {}).get("cmd", ""))))


def _is_mutating(name, args) -> bool:
    args = args or {}
    if _is_verify(name, args):
        return False
    if name == "edit_file":
        return True
    if name == "file_op":
        return str(args.get("action") or args.get("op") or "").lower() not in ("read", "cat", "view", "")
    if name == "shell_exec":
        cmd = str(args.get("cmd", ""))
        if re.search(r">|&&|;", cmd):
            return True
        return not _READONLY_SHELL.search(cmd)
    return False


def _git_snapshot(directory, task_id, label):
    """Record the working tree as a ref (refs/harvis/<task>/<label>) without touching the
    branch, index or files. Restore with `git stash apply <sha>`. Untracked files are not included."""
    try:
        if not directory or not os.path.isdir(directory):
            return None

        def g(*a):
            return subprocess.run(["git", "-C", directory, *a], capture_output=True, text=True, timeout=20)

        if g("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
            return None
        sha = g("stash", "create").stdout.strip() or g("rev-parse", "HEAD").stdout.strip()
        if not sha:
            return None
        g("update-ref", f"refs/harvis/{task_id}/{label}", sha)
        return sha
    except Exception:
        return None


async def _execute_batch(work, task_mgr, default_cwd=None, hooks_for=None):
    """Run a list of (name, args). Consecutive read-only calls run concurrently; anything
    that may change state runs alone, in order. Returns [(result, seconds)] in call order."""
    def with_cwd(name, args):
        """Run project-relative work in the session's project directory."""
        if not default_cwd or not isinstance(args, dict):
            return args
        a = dict(args)
        rel = lambda p: p if os.path.isabs(os.path.expanduser(str(p))) else os.path.join(default_cwd, str(p))
        if name in ("shell_exec", "run_tests"):
            if not any(a.get(k) for k in ("cwd", "workdir", "dir")):
                a["cwd"] = default_cwd
        elif name in ("grep", "glob_files"):
            a["path"] = rel(a["path"]) if a.get("path") else default_cwd
        elif name in ("read_file", "edit_file", "list_dir") and a.get("path"):
            a["path"] = rel(a["path"])
        elif name == "file_op" and a.get("path"):
            a["path"] = rel(a["path"])
            if a.get("destination"):
                a["destination"] = rel(a["destination"])
        return a

    async def one(name, args, idx=0):
        t0 = time.time()
        token = None
        try:
            hooks = hooks_for(idx, name) if hooks_for else None
            if hooks:                                   # visible to the worker thread (to_thread copies the context)
                from tools.system import STREAM_HOOKS
                token = STREAM_HOOKS.set(hooks)
        except Exception:
            token = None
        try:
            return await _one(name, with_cwd(name, args), t0)
        except Exception as e:  # a tool blowing up is an observation for the agent, not the end of the task
            logger.warning(f"Tool {name} raised: {e}")
            return {"success": False, "output": "", "error": f"{type(e).__name__}: {e}"}, round(time.time() - t0, 2)
        finally:
            if token is not None:
                try:
                    from tools.system import STREAM_HOOKS
                    STREAM_HOOKS.reset(token)
                except Exception:
                    pass

    async def _one(name, args, t0):
        if name == "shell_exec" and not (args or {}).get("cmd"):
            res = {"success": False, "error": (
                "FATAL: shell_exec requires 'cmd'. You passed empty arguments {}. "
                "Do NOT retry shell_exec with empty parameters! If you need to run a command, provide 'cmd'. "
                "Otherwise, stop calling tools and reply to the user directly in plain text with your answer.")}
        elif name == UPDATE_TASK_TOOL["name"]:
            content = str((args or {}).get("content", "")).strip()
            if content:
                task_mgr.update_plan(content)
                res = {"success": True, "output": "task.md updated."}
            else:
                res = {"success": False, "error": "update_task needs non-empty 'content'."}
        else:
            res = await asyncio.to_thread(dispatch, name, args)
        return res, round(time.time() - t0, 2)

    out, i = [], 0
    while i < len(work):
        if work[i][0] in READ_ONLY_TOOLS:
            j = i
            while j < len(work) and work[j][0] in READ_ONLY_TOOLS:
                j += 1
            out.extend(await asyncio.gather(*[one(n, a, k) for k, (n, a) in enumerate(work[i:j], start=i)]))
            i = j
        else:
            out.append(await one(work[i][0], work[i][1], i))
            i += 1
    return out


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


def _clip_mid(text: str, limit: int) -> str:
    """Keep the start and end of a long message; say how much was dropped."""
    if len(text) <= limit:
        return text
    head, tail = int(limit * 0.6), int(limit * 0.4)
    return f"{text[:head]}\n[... {len(text) - head - tail} characters trimmed from this earlier message ...]\n{text[-tail:]}"


def _trim_prefix(prefix: list, budget: int) -> list:
    """Cap the earlier-chat context. Without this every turn re-sent every previous answer in full
    (up to 20 messages), so each reply took longer than the last, and the step window below was
    squeezed to nothing to make room. Newest messages keep the most detail."""
    if not prefix or not budget:
        return list(prefix or [])
    kept, used = [], 0
    for i, m in enumerate(reversed(prefix)):
        c = m.get("content")
        if not isinstance(c, str):
            kept.append(m)
            continue
        limit = 3000 if i < 2 else 1200 if i < 6 else 500
        c = _clip_mid(c, limit)
        if kept and used + len(c) > budget:
            break
        kept.append({**m, "content": c})
        used += len(c)
    kept.reverse()
    while len(kept) > 1 and kept[0].get("role") == "assistant":   # providers expect a user turn first
        kept.pop(0)
    return kept


_MASK_MARK = "[older output trimmed"


def _mask_old_observations(history: list, goal_idx: int, keep_recent: int, keep_chars: int) -> None:
    """Shrink all but the newest `keep_recent` tool outputs to a short stub (in place).
    Every step used to carry up to 6,000 characters of output forever, so the prompt grew with each
    step and each model call took longer than the last. The agent still sees what it did (the call
    and the harness facts); the full text is in trace.jsonl and can be re-read with a tool."""
    if not keep_chars:
        return
    idxs = [i for i in range(goal_idx + 1, len(history))
            if history[i].get("role") == "tool" and isinstance(history[i].get("content"), str)]
    for i in (idxs[:-keep_recent] if keep_recent else idxs):
        c = history[i]["content"]
        if _MASK_MARK in c or len(c) <= keep_chars + 200:
            continue
        cut = c.rfind("\n[harness:")                      # keep the harness facts line intact
        body, note = (c[:cut], c[cut:]) if cut != -1 else (c, "")
        if len(body) > keep_chars:
            body = f"{body[:keep_chars]}\n{_MASK_MARK}: {len(body) - keep_chars} more characters, see trace.jsonl]"
        history[i] = {**history[i], "content": body + note}


def _history_chars(history: list) -> int:
    return sum(_content_chars(m.get("content")) for m in history)


def _default_engine(history, *, system_prompt, mode, stream_callback, extra_tools):
    """One model turn via the router's provider chain (imported lazily)."""
    from brain.router import complete
    return complete(history, system_prompt=system_prompt, mode=mode,
                    stream_callback=stream_callback, extra_tools=extra_tools)


class HarnessRunner:
    def __init__(self, task_id: str = None, session_id: str = None, user_id: str = None,
                 mode: str = "normal", engine=None, system_prompt: str = None,
                 history_prefix: list = None):
        """engine: callable(history, *, system_prompt, mode, stream_callback, extra_tools) -> dict | None.
        system_prompt: persona/context placed before the harness prompt.
        history_prefix: earlier chat messages ({"role","content"}) the agent should know about."""
        self.task_id = task_id or f"task_{uuid.uuid4().hex[:8]}"
        self.session_id = session_id or "general"
        self.user_id = user_id
        self.mode = mode
        self.engine = engine or _default_engine
        self.persona = system_prompt
        self.history_prefix = list(history_prefix or [])
        self.task_mgr = TaskManager(self.task_id, session_id=self.session_id, user_id=self.user_id)

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

    async def run(self, goal, *args, **kwargs) -> dict:
        """Run the task. An unexpected internal error becomes a result, never an exception
        that escapes to the caller and silently kills a long task."""
        self._step = 0
        try:
            return await self._run_inner(goal, *args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Harness crashed")
            try:
                self.task_mgr.append_trace({"type": "crash", "error": repr(e)})
            except Exception:
                pass
            return self._result("error", False, self._step,
                                message=f"Internal error: {type(e).__name__}: {e}")

    async def _run_inner(
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

        active_dir_info = ""
        snap_dir = None
        seq = edit_seq = verify_seq = 0
        last_snap_step = -999
        finish_checked = False
        nudges = 0
        try:
            from db.store import get_session_info
            sinfo = get_session_info(self.session_id)
            if sinfo and sinfo.get("active_dir"):
                active_dir_info = f"\n[harness] Active Project Directory: {sinfo.get('active_dir')}"
                snap_dir = sinfo.get("active_dir")
        except Exception:
            pass

        memory_block = ""
        try:
            from brain.continuity import build_working_memory_prompt
            memory_block = build_working_memory_prompt(self.session_id, snap_dir)
        except Exception as mb_err:
            logger.debug(f"Continuity memory block generation failed: {mb_err}")

        header = (
            f"[harness] Session ID: {self.session_id}{active_dir_info}\n"
            f"[harness] Task directory: {self.task_mgr.dir}\n"
            f"[harness] Full untruncated step trace: {self.task_mgr.dir}/trace.jsonl "
            f"(older steps leave your context window but stay there).\n"
            f"[harness] Budget: "
            f"{max_steps or 'unlimited'} steps, {max_duration_sec or 'unlimited'}s."
        )
        if memory_block:
            header += f"\n\n{memory_block}"
        if isinstance(goal, str):
            first = {"role": "user", "content": f"{goal}\n\n{header}"}
        else:
            first = {"role": "user", "content": list(goal) + [{"type": "text", "text": header}]}
        prefix = _trim_prefix(self.history_prefix, settings.PREFIX_CHARS)
        history = prefix + [first]
        goal_idx = len(prefix)
        await emit({"type": "init", "task_id": self.task_id, "goal": goal_text, "plan": self.task_mgr.read_plan()})

        step = 0
        while True:
            step += 1
            self._step = step
            try:
                from db.store import touch_harvis_busy
                touch_harvis_busy()
            except Exception:
                pass
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

            _mask_old_observations(history, goal_idx, settings.KEEP_RECENT_OBS, settings.OLD_OBS_CHARS)
            # Roll the window: drop oldest (assistant call, tool result) pairs, keep the goal.
            while (settings.CONTEXT_CHARS and _history_chars(history[goal_idx:]) > settings.CONTEXT_CHARS
                   and len(history) > goal_idx + 3):
                del history[goal_idx + 1:goal_idx + 3]

            await emit({"type": "thinking", "step": step})
            turn_prompt = system_prompt + "\n\n" + _task_block(self.task_mgr)
            response = None
            t_model = time.time()
            attempts = max(1, settings.ENGINE_RETRIES)
            for attempt in range(1, attempts + 1):
                try:
                    response = await asyncio.to_thread(
                        _call_engine, self.engine, history, system_prompt=turn_prompt, mode=self.mode,
                        stream_callback=stream_callback, extra_tools=[FINISH_TOOL, UPDATE_TASK_TOOL],
                    )
                except Exception as err:
                    if cancel_event and cancel_event.is_set():
                        return self._result("cancelled", False, step, message="Task cancelled by user.")
                    logger.warning(f"Engine call failed in harness loop (attempt {attempt}/{attempts}): {err}")
                    response = None
                if response or attempt == attempts:
                    break
                await asyncio.sleep(min(30, settings.ENGINE_RETRY_BACKOFF * attempt))
                if cancel_event and cancel_event.is_set():
                    return self._result("cancelled", False, step, message="Task cancelled by user.")

            prompt_chars = _history_chars(history) + len(turn_prompt)
            self.task_mgr.append_trace({"step": step, "type": "model_call", "prompt_chars": prompt_chars,
                                        "history_msgs": len(history), "seconds": round(time.time() - t_model, 2),
                                        "ok": bool(response)})
            logger.info(f"[{self.task_id}] step {step}: model call {time.time() - t_model:.1f}s, "
                        f"~{prompt_chars} chars of history+system (tool schemas are added on top)")

            if not response:
                if cancel_event and cancel_event.is_set():
                    return self._result("cancelled", False, step, message="Task cancelled by user.")
                return self._result("provider_error", False, step,
                                    message="All engine providers failed during harness execution.")

            # Plain text, no tool call. If the agent only narrated ("I'm on it") while its own task file
            # still has unchecked items, push it on - but only a couple of times in a row, never when it
            # is asking the user something, and never past the step budget. Otherwise the text is the answer.
            if response.get("type") != "tool_call":
                text = (response.get("content") or "").strip()
                plan = self.task_mgr.read_plan()
                open_items = len(re.findall(r"^\s*[-*+]\s+\[ \]", plan or "", re.M))
                promising = any(p in text.lower() for p in _PROMISE_PHRASES)
                asking = text.rstrip().endswith("?")
                within_budget = not max_steps or step < max_steps - 1
                if (settings.MAX_NUDGES and nudges < settings.MAX_NUDGES and not asking
                        and (open_items or promising) and within_budget):
                    nudges += 1
                    self.task_mgr.append_trace({"step": step, "type": "nudge", "n": nudges, "open_items": open_items})
                    await emit({"type": "catchup", "step": step, "text": text, "plan": plan})
                    history.append({"role": "assistant", "content": text or "(no reply)"})
                    why = (f"task.md still has {open_items} unchecked item(s)" if open_items
                           else "you said you were working on it")
                    history.append({"role": "user", "content": (
                        f"[System Guidance: no tool was called, but {why}. Do not stop to narrate. "
                        "Call the next tool now, update_task if the plan changed, or call finish if you are "
                        "done or blocked.]")})
                    continue

                self.task_mgr.append_trace({"step": step, "type": "final_text", "content": text})
                await emit({"type": "completed", "step": step, "summary": text, "plan": self.task_mgr.read_plan()})
                # success is None: the agent didn't declare one and the harness won't invent one.
                return self._result("answered", None, step, output=text)


            nudges = 0                       # a real tool call resets the nudge counter
            calls = [(response.get("tool_name"), response.get("tool_args", {}) or {})]
            calls += [(c.get("tool_name"), c.get("tool_args", {}) or {})
                      for c in (response.get("extra_calls") or []) if c.get("tool_name")]
            finish_call = next(((n, a) for n, a in calls if n == FINISH_TOOL["name"]), None)
            work = [(n, a) for n, a in calls if n != FINISH_TOOL["name"]]

            if work:
                for i, (n, a) in enumerate(work):
                    await emit({"type": "acting", "step": step, "call_id": f"{step}.{i}", "tool_name": n,
                                "tool_args": a, "plan": self.task_mgr.read_plan()})

                # Snapshot the working tree before the first change, then at most every 5 steps.
                if snap_dir and any(_is_mutating(n, a) for n, a in work) and step - last_snap_step >= 5:
                    sha = await asyncio.to_thread(_git_snapshot, snap_dir, self.task_id, f"step{step}")
                    last_snap_step = step
                    if sha:
                        self.task_mgr.append_trace({"step": step, "type": "snapshot", "ref": f"refs/harvis/{self.task_id}/step{step}", "sha": sha})

                loop = asyncio.get_running_loop()

                def hooks_for(i, name):
                    """Live output + cancellation for a command; other tools get nothing."""
                    if name != "shell_exec":
                        return None
                    last = [0.0]

                    def on_output(text):                       # called from the tool's worker thread
                        now = time.monotonic()
                        if now - last[0] < 0.5:
                            return
                        last[0] = now
                        loop.call_soon_threadsafe(lambda: loop.create_task(emit(
                            {"type": "output", "step": step, "call_id": f"{step}.{i}",
                             "tool_name": name, "text": text})))
                    return {"on_output": on_output, "cancel_event": cancel_event}

                results = await _execute_batch(work, self.task_mgr, snap_dir, hooks_for)

                for call_idx, ((tool_name, tool_args), (tool_res, took)) in enumerate(zip(work, results)):
                    seq += 1
                    ok = bool(tool_res.get("success", False))
                    raw = tool_res.get("output", "") or ""
                    err = tool_res.get("error", "") or ""
                    raw_text = raw if not err else f"{raw}\n[stderr/error]: {err}".strip()

                    # Dynamically track directory changes from session_manager or cd-like executions
                    if ok:
                        if tool_name == "session_manager" and isinstance(tool_args, dict) and tool_args.get("active_dir"):
                            new_ad = tool_args["active_dir"]
                            if os.path.isdir(new_ad):
                                snap_dir = new_ad
                        elif tool_name == "shell_exec" and isinstance(tool_args, dict):
                            cmd_str = (tool_args.get("cmd") or "").strip()
                            cd_m = re.match(r"^cd\s+([^\s;&|]+)", cmd_str)
                            if cd_m:
                                target_p = os.path.expanduser(cd_m.group(1))
                                if not os.path.isabs(target_p) and snap_dir:
                                    target_p = os.path.join(snap_dir, target_p)
                                target_p = os.path.abspath(target_p)
                                if os.path.isdir(target_p):
                                    snap_dir = target_p
                                    try:
                                        from db.store import update_session
                                        update_session(self.session_id, active_dir=snap_dir)
                                    except Exception:
                                        pass

                        # Record touched files and commands into session working memory
                        try:
                            from brain.continuity import record_touched_file, record_command_action
                            if tool_name in ("write_file", "edit_file") and isinstance(tool_args, dict) and tool_args.get("path"):
                                record_touched_file(self.session_id, tool_args["path"], action=tool_name)
                            elif tool_name == "file_op" and isinstance(tool_args, dict) and tool_args.get("path"):
                                act = tool_args.get("action", "file_op")
                                if act in ("write", "append", "create", "edit"):
                                    record_touched_file(self.session_id, tool_args["path"], action=act)
                            elif tool_name == "shell_exec" and isinstance(tool_args, dict) and tool_args.get("cmd"):
                                record_command_action(self.session_id, tool_args["cmd"], exit_code=0 if ok else 1, output_summary=raw_text)
                        except Exception as c_err:
                            logger.debug(f"Continuity recording failed: {c_err}")

                    if ok and _is_verify(tool_name, tool_args):
                        verify_seq = seq
                    elif ok and _is_mutating(tool_name, tool_args):
                        edit_seq = seq

                    self.task_mgr.append_trace({
                        "step": step, "type": "action", "tool": tool_name, "args": tool_args,
                        "success": ok, "seconds": took, "output": raw_text,
                    })

                    # Facts for the agent. No instructions.
                    sig = _result_sig(tool_name, tool_args, raw_text)
                    if tool_name == UPDATE_TASK_TOOL["name"]:
                        sig += f":{seq}"  # rewriting the plan is progress, never a repeat
                    recent.append(sig)
                    repeats = sum(1 for s in recent if s == sig)
                    facts = [f"step {step}" + (f"/{max_steps}" if max_steps else ""),
                             f"{int(time.time() - start)}s elapsed" + (f"/{max_duration_sec}s" if max_duration_sec else ""),
                             f"success={ok}", f"{took}s"]
                    if len(work) > 1:
                        facts.append(f"batch of {len(work)} calls")
                    if not ok:
                        facts.append("ACTION FAILED: Inspect the error/stderr above. Do NOT surrender or say you couldn't complete it. Try an alternative tool, search/find commands, or different paths to resolve Harold's goal.")
                    if tool_res.get("timed_out"):
                        facts.append("command did not exit on its own and was killed")
                    if settings.REPEAT_NOTICE and repeats >= settings.REPEAT_NOTICE:
                        facts.append(f"WARNING: this exact action has run {repeats} times with the same result - "
                                     f"change arguments, try another approach, or finish")
                    note = "[harness: " + ", ".join(facts) + "]"

                    assistant_payload = {
                        "type": "tool_call",
                        "tool_name": tool_name,
                        "tool_args": tool_args
                    }
                    if response.get("thought_signature"):
                        assistant_payload["thought_signature"] = response["thought_signature"]

                    history.append({"role": "assistant",
                                    "content": json.dumps(assistant_payload)})
                    history.append({"role": "tool",
                                    "content": f"{_clip(raw_text, settings.OBS_CHARS)}\n{note}"})

                    await emit({"type": "checked", "step": step, "call_id": f"{step}.{call_idx}",
                                "tool_name": tool_name, "tool_args": tool_args, "success": ok,
                                "exit_code": (None if tool_res.get("cancelled") else
                                              tool_res.get("code", 0 if ok else 1)),
                                "check_passed": ok, "details": note[:200], "output": raw_text,
                                "plan": self.task_mgr.read_plan()})

                    # Hard stop only at REPEAT_ABORT identical action+result pairs (0 = never).
                    if settings.REPEAT_ABORT and repeats >= settings.REPEAT_ABORT:
                        return self._result("stuck", False, step,
                                            message=f"Action '{tool_name}' repeated {repeats} times with identical args and identical output; breaking loop.",
                                            notes=self.task_mgr.read_notes())

            # The agent decides it is done.
            if finish_call:
                tool_args = finish_call[1]
                summary = str(tool_args.get("summary", "")).strip()
                declared = tool_args.get("success")

                # If the agent called finish without a summary, generate an autonomous debrief turn
                if not summary and (work or edit_seq > 0 or verify_seq > 0):
                    history.append({"role": "assistant", "content": json.dumps(
                        {"type": "tool_call", "tool_name": "finish", "tool_args": tool_args})})
                    history.append({
                        "role": "tool",
                        "content": "[harness: finish requires a descriptive summary for Harold. Please reply in plain text describing exactly what you completed and verified.]"
                    })
                    continue

                # One-time check: claiming success after editing without a passing check since.
                # The agent still decides: calling finish again goes through.
                if declared is True and edit_seq > verify_seq and not finish_checked:
                    finish_checked = True
                    msg = ("[harness: finish not accepted yet. You changed files after your last passing "
                           "check (run_tests, build, lint). Run one now, or call finish again to confirm "
                           "(use success=false if you could not verify).]")
                    self.task_mgr.append_trace({"step": step, "type": "finish_deferred", "summary": summary})
                    history.append({"role": "assistant", "content": json.dumps(
                        {"type": "tool_call", "tool_name": "finish", "tool_args": tool_args})})
                    history.append({"role": "tool", "content": msg})
                    continue

                self.task_mgr.append_trace({"step": step, "type": "finish", "success": declared, "summary": summary})
                await emit({"type": "completed", "step": step, "summary": summary, "plan": self.task_mgr.read_plan()})
                return self._result("finished", declared if isinstance(declared, bool) else None,
                                    step, output=summary)
