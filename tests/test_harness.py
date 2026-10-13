"""Harness tests with a scripted fake provider (no gateway, no network)."""
import asyncio
import sys
import types

import pytest

# The harness only needs tools.registry.dispatch; stub it only if the desktop stack is unavailable.
try:
    import tools.registry  # noqa: F401
except Exception:
    _reg = types.ModuleType("tools.registry")
    _reg.dispatch = lambda name, args: {"success": True, "output": "ok"}
    sys.modules["tools.registry"] = _reg

from brain import harness, settings  # noqa: E402
from brain.state_manager import TaskManager  # noqa: E402


def call(name, **args):
    return {"type": "tool_call", "tool_name": name, "tool_args": args, "content": None}


def text(s):
    return {"type": "text", "content": s}


def run_scripted(monkeypatch, tmp_path, script, dispatch=None, init=None, goal="goal", **kw):
    """script: list of provider responses. Returns (result, histories seen by the model)."""
    init = init or {}
    seen = []
    it = iter(script)

    def fake_call(history, **k):
        seen.append([dict(m) for m in history])
        return next(it)

    monkeypatch.setattr(harness, "dispatch", dispatch or (lambda n, a: {"success": True, "output": "ok"}))
    r = harness.HarnessRunner(task_id="t", engine=fake_call, **init)
    r.task_mgr = TaskManager("t", tasks_dir=str(tmp_path))
    return asyncio.run(r.run(goal, **kw)), seen


def test_agent_declared_failure_is_respected(monkeypatch, tmp_path):
    res, _ = run_scripted(monkeypatch, tmp_path, [
        call("shell_exec", cmd="ls"),
        call("finish", success=False, summary="blocked: no permission"),
    ])
    assert res["status"] == "finished" and res["success"] is False
    assert res["output"] == "blocked: no permission"


def test_text_after_failed_action_is_not_marked_success(monkeypatch, tmp_path):
    res, _ = run_scripted(
        monkeypatch, tmp_path,
        [call("shell_exec", cmd="false"), text("I could not do it.")],
        dispatch=lambda n, a: {"success": False, "error": "exit 1"},
    )
    assert res["status"] == "answered" and res["success"] is None


def test_completion_phrase_means_nothing(monkeypatch, tmp_path):
    # Old harness treated any text containing "task complete" as success.
    res, _ = run_scripted(monkeypatch, tmp_path, [text("task complete? no, it failed")])
    assert res["success"] is None


def test_agent_sees_its_own_history(monkeypatch, tmp_path):
    _, seen = run_scripted(
        monkeypatch, tmp_path,
        [call("shell_exec", cmd="cat a"), call("shell_exec", cmd="cat b"), call("finish", summary="done")],
        dispatch=lambda n, a: {"success": True, "output": f"result-of-{a['cmd']}"},
    )
    third = "\n".join(str(m["content"]) for m in seen[2])
    assert "result-of-cat a" in third and "result-of-cat b" in third


def test_repeats_are_reported_then_abort(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "REPEAT_NOTICE", 3)
    monkeypatch.setattr(settings, "REPEAT_ABORT", 5)
    res, seen = run_scripted(monkeypatch, tmp_path, [call("shell_exec", cmd="x")] * 10)
    assert res["status"] == "stuck"
    assert "this exact action has run" in str(seen[-1][-1]["content"])


def test_budget_is_configurable_and_zero_means_unlimited(monkeypatch, tmp_path):
    res, _ = run_scripted(monkeypatch, tmp_path, [call("shell_exec", cmd=str(i)) for i in range(3)],
                          max_steps=2)
    assert res["status"] == "step_limit_reached"
    res, _ = run_scripted(monkeypatch, tmp_path,
                          [call("shell_exec", cmd=str(i)) for i in range(30)] + [call("finish", summary="ok")],
                          max_steps=0)
    assert res["status"] == "finished"


def test_long_output_keeps_head_and_tail():
    out = harness._clip("A" * 5000 + "B" * 5000, 1000)
    assert out.startswith("A") and out.rstrip().endswith("B") and "omitted" in out


def test_shell_runs_command_as_written():
    pytest.importorskip("tools.session")
    from tools.system import shell_exec
    res = shell_exec(cmd="echo top ping watch")
    assert res["success"] and res["output"].strip() == "top ping watch"


def test_chat_history_prefix_and_persona_reach_the_model(monkeypatch, tmp_path):
    seen_prompts = []
    prefix = [{"role": "user", "content": "my name is H"}, {"role": "assistant", "content": "hi H"}]

    def engine(history, **k):
        seen_prompts.append(k["system_prompt"])
        return text("hello")

    monkeypatch.setattr(harness, "dispatch", lambda n, a: {"success": True, "output": "ok"})
    r = harness.HarnessRunner(task_id="t", engine=engine, system_prompt="PERSONA", history_prefix=prefix)
    r.task_mgr = TaskManager("t", tasks_dir=str(tmp_path))
    res = asyncio.run(r.run("what's my name?"))
    assert res["output"] == "hello"
    assert seen_prompts[0].startswith("PERSONA") and "finish" in seen_prompts[0]


def test_multimodal_goal_and_window_ignore_image_payload(monkeypatch, tmp_path):
    img = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 500000}}
    goal = [{"type": "text", "text": "what is this?"}, img]
    res, seen = run_scripted(
        monkeypatch, tmp_path,
        [call("shell_exec", cmd="a"), call("shell_exec", cmd="b"), text("a cat")], goal=goal)
    assert res["output"] == "a cat"
    # image payload must not count toward the window: steps stay in context
    assert len(seen[2]) == 5 and seen[2][0]["content"][1] is img


def test_async_progress_callback_and_events(monkeypatch, tmp_path):
    events = []

    async def cb(ev):
        events.append(ev["type"])

    run_scripted(monkeypatch, tmp_path, [call("shell_exec", cmd="a"), call("finish", summary="x")],
                 progress_callback=cb)
    assert events == ["init", "thinking", "acting", "checked", "thinking", "completed"]


def test_cancel_during_model_call(monkeypatch, tmp_path):
    import threading
    cancel = threading.Event()

    def engine(history, **k):
        cancel.set()
        raise RuntimeError("Cancelled by user")

    r = harness.HarnessRunner(task_id="t", engine=engine)
    r.task_mgr = TaskManager("t", tasks_dir=str(tmp_path))
    res = asyncio.run(r.run("goal", cancel_event=cancel))
    assert res["status"] == "cancelled"
