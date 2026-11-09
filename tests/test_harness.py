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


def test_agent_writes_and_sees_its_own_task_file(monkeypatch, tmp_path):
    res, seen = run_scripted(monkeypatch, tmp_path, [
        call("update_task", content="# Goal\n- [ ] step one\n- [ ] step two"),
        call("finish", summary="done"),
    ])
    assert res["status"] == "finished"
    assert "step one" in (tmp_path / "t" / "task.md").read_text()
    assert "step one" in res["plan"]


def test_task_file_reaches_system_prompt_every_turn(monkeypatch, tmp_path):
    prompts = []
    script = iter([call("update_task", content="# Goal\n- [ ] REMEMBER-ME"), text("ok")])

    def engine(history, **k):
        prompts.append(k["system_prompt"])
        return next(script)

    monkeypatch.setattr(harness, "dispatch", lambda n, a: {"success": True, "output": "ok"})
    r = harness.HarnessRunner(task_id="t", engine=engine)
    r.task_mgr = TaskManager("t", tasks_dir=str(tmp_path))
    asyncio.run(r.run("goal"))
    assert "REMEMBER-ME" not in prompts[0] and "REMEMBER-ME" in prompts[1]


def test_edit_test_loop_is_not_counted_as_stuck(monkeypatch, tmp_path):
    # Same command, different output each time = progress, not a loop.
    n = {"i": 0}

    def disp(name, args):
        n["i"] += 1
        return {"success": True, "output": f"{20 - n['i']} failures left"}

    res, _ = run_scripted(monkeypatch, tmp_path,
                          [call("shell_exec", cmd="pytest")] * 12 + [call("finish", summary="green")], dispatch=disp)
    assert res["status"] == "finished"


def test_engine_failure_is_retried(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ENGINE_RETRIES", 3)
    monkeypatch.setattr(settings, "ENGINE_RETRY_BACKOFF", 0)
    attempts = {"n": 0}

    def engine(history, **k):
        attempts["n"] += 1
        return None if attempts["n"] < 3 else text("recovered")

    monkeypatch.setattr(harness, "dispatch", lambda n, a: {"success": True, "output": "ok"})
    r = harness.HarnessRunner(task_id="t", engine=engine)
    r.task_mgr = TaskManager("t", tasks_dir=str(tmp_path))
    res = asyncio.run(r.run("goal"))
    assert res["output"] == "recovered" and attempts["n"] == 3


def batch(*calls, extra_text=None):
    first = calls[0]
    r = dict(first)
    r["extra_calls"] = [{"tool_name": c["tool_name"], "tool_args": c["tool_args"]} for c in calls[1:]]
    return r


def test_batch_runs_every_call_and_returns_each_result(monkeypatch, tmp_path):
    ran = []

    def disp(name, args):
        ran.append((name, args.get("path") or args.get("cmd")))
        return {"success": True, "output": f"out-{args.get('path') or args.get('cmd')}"}

    res, seen = run_scripted(monkeypatch, tmp_path, [
        batch(call("read_file", path="a"), call("read_file", path="b"), call("grep", pattern="x", path="c")),
        call("finish", summary="done"),
    ], dispatch=disp)
    assert res["status"] == "finished"
    assert [r[0] for r in ran] == ["read_file", "read_file", "grep"]
    last = "\n".join(str(m["content"]) for m in seen[1])
    assert "out-a" in last and "out-b" in last and "batch of 3 calls" in last


def test_read_only_calls_run_concurrently_but_writes_stay_ordered(monkeypatch, tmp_path):
    import threading, time as _t
    order, active, peak = [], {"n": 0}, {"n": 0}
    lock = threading.Lock()

    def disp(name, args):
        with lock:
            active["n"] += 1
            peak["n"] = max(peak["n"], active["n"])
        _t.sleep(0.05)
        with lock:
            active["n"] -= 1
            order.append(name)
        return {"success": True, "output": "ok"}

    run_scripted(monkeypatch, tmp_path, [
        batch(call("read_file", path="a"), call("read_file", path="b"), call("edit_file", path="a", old_str="x", new_str="y"),
              call("shell_exec", cmd="echo hi > f")),
        text("ok")], dispatch=disp)
    assert peak["n"] == 2                      # the two reads overlapped, nothing else did
    assert order[-2:] == ["edit_file", "shell_exec"]


def test_finish_after_unverified_edit_is_deferred_once(monkeypatch, tmp_path):
    res, seen = run_scripted(monkeypatch, tmp_path, [
        call("edit_file", path="a", old_str="x", new_str="y"),
        call("finish", success=True, summary="fixed"),   # no check ran since the edit
        call("finish", success=True, summary="fixed"),   # agent confirms: goes through
    ])
    assert res["status"] == "finished" and res["success"] is True
    assert any("finish not accepted yet" in str(m["content"]) for m in seen[2])


def test_finish_after_passing_check_is_accepted_immediately(monkeypatch, tmp_path):
    res, seen = run_scripted(monkeypatch, tmp_path, [
        call("edit_file", path="a", old_str="x", new_str="y"),
        call("run_tests"),
        call("finish", success=True, summary="fixed"),
    ])
    assert res["status"] == "finished" and len(seen) == 3


def test_git_snapshot_records_ref_without_touching_worktree(tmp_path):
    import subprocess
    d = str(tmp_path)
    run = lambda *a: subprocess.run(["git", "-C", d, *a], capture_output=True, text=True)
    run("init", "-q"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
    (tmp_path / "f.txt").write_text("one"); run("add", "."); run("commit", "-qm", "init")
    (tmp_path / "f.txt").write_text("two")        # uncommitted change
    sha = harness._git_snapshot(d, "task1", "step1")
    assert sha and run("rev-parse", "refs/harvis/task1/step1").stdout.strip() == sha
    assert (tmp_path / "f.txt").read_text() == "two"  # worktree untouched
    assert harness._git_snapshot(str(tmp_path / "nope"), "t", "s") is None


def test_blocklist_catches_common_wipe_variants():
    pytest.importorskip("tools.session")
    from tools.system import shell_exec
    for cmd in ["rm -rf ~", "rm -fr /", "rm -rf $HOME", "rm -rf /*", "sudo rm -rf / --no-preserve-root",
                "find / -delete", "mkfs.ext4 /dev/sda1", "dd if=x of=/dev/sda"]:
        res = shell_exec(cmd=cmd)
        assert not res["success"] and "blocked" in res["error"].lower(), cmd
    from tools.system import BLOCK_PATTERNS
    for ok_cmd in ["rm -rf ./build/tmp-xyz", "rm -rf node_modules", "rm file.txt"]:
        assert not any(p.search(ok_cmd) for p in BLOCK_PATTERNS), ok_cmd  # scoped deletes are not hard-blocked


def test_daemon_mode_denies_destructive_by_default(monkeypatch):
    pytest.importorskip("tools.session")
    import tools.system as sysmod
    monkeypatch.setattr(sysmod, "confirmation_callback", None)
    monkeypatch.setattr(sysmod.sys, "stdin", None)
    monkeypatch.delenv("HARVIS_AUTO_APPROVE", raising=False)
    assert sysmod.request_confirmation("rm something") is False
    monkeypatch.setenv("HARVIS_AUTO_APPROVE", "1")
    assert sysmod.request_confirmation("rm something") is True
