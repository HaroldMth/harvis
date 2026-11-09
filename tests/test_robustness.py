"""Bugs that could silently end or corrupt a long unattended run."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import brain.harness as harness  # noqa: E402
import brain.router as router  # noqa: E402
import tools.registry as registry  # noqa: E402
from brain.state_manager import TaskManager  # noqa: E402
from db import store  # noqa: E402


def call(name, **a):
    return {"type": "tool_call", "tool_name": name, "tool_args": a, "content": None}


def run_harness(monkeypatch, tmp_path, script, dispatch=None, session_dir=None):
    it = iter(script)

    def engine(history, **k):
        try:
            return next(it)
        except StopIteration:
            raise RuntimeError("script exhausted")

    monkeypatch.setattr(harness, "dispatch", dispatch or (lambda n, a: {"success": True, "output": "ok"}))
    if session_dir:
        monkeypatch.setattr("db.store.get_session_info", lambda sid: {"active_dir": session_dir})
    r = harness.HarnessRunner(task_id="t", engine=engine)
    r.task_mgr = TaskManager("t", tasks_dir=str(tmp_path))
    return asyncio.run(r.run("goal")), r


# ── the missing `import time` made the busy flag a silent no-op ──────────────

def test_busy_flag_actually_works_and_is_reference_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "BUSY_FILE", str(tmp_path / "busy.json"))
    monkeypatch.setattr(store, "_BUSY_COUNT", 0)
    assert not store.is_harvis_busy()
    store.set_harvis_busy(True)
    store.set_harvis_busy(True)           # two overlapping tasks
    assert store.is_harvis_busy()
    store.set_harvis_busy(False)          # one finishing must not clear the other's flag
    assert store.is_harvis_busy()
    store.set_harvis_busy(False)
    assert not store.is_harvis_busy()


def test_busy_flag_survives_long_tasks_but_not_crashed_ones(tmp_path, monkeypatch):
    import json, time
    monkeypatch.setattr(store, "BUSY_FILE", str(tmp_path / "busy.json"))
    monkeypatch.setattr(store, "_BUSY_COUNT", 0)
    store.set_harvis_busy(True)
    # 5 minutes without a refresh (the old limit was 120s, which un-busied every task over 2 minutes)
    d = json.load(open(store.BUSY_FILE)); d["ts"] = time.time() - 300; json.dump(d, open(store.BUSY_FILE, "w"))
    assert store.is_harvis_busy()
    store.touch_harvis_busy()
    d = json.load(open(store.BUSY_FILE)); assert time.time() - d["ts"] < 5
    d["ts"] = time.time() - 4000; json.dump(d, open(store.BUSY_FILE, "w"))
    assert not store.is_harvis_busy()      # nobody refreshed it for over an hour: assume crashed


def test_sqlite_uses_wal_and_long_busy_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "x.db"))
    conn = store.get_connection()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 30000
    conn.close()


# ── dispatch must never raise ────────────────────────────────────────────────

def test_dispatch_turns_every_tool_failure_into_a_result(monkeypatch):
    def boom(**k): raise RuntimeError("kaboom")
    def none(**k): return None
    def text(**k): return "just a string"
    def exits(**k): raise SystemExit(3)
    monkeypatch.setitem(registry.TOOL_DISPATCH, "boom", boom)
    monkeypatch.setitem(registry.TOOL_DISPATCH, "none", none)
    monkeypatch.setitem(registry.TOOL_DISPATCH, "text", text)
    monkeypatch.setitem(registry.TOOL_DISPATCH, "exits", exits)
    r = registry.dispatch("boom", {}); assert r["success"] is False and "kaboom" in r["error"]
    r = registry.dispatch("none", {}); assert r["success"] is True
    r = registry.dispatch("text", {}); assert r["output"] == "just a string"
    r = registry.dispatch("exits", {}); assert r["success"] is False
    assert registry.dispatch("no_such_tool", {})["success"] is False


def test_a_locked_log_database_does_not_fail_a_tool_that_already_ran(monkeypatch):
    def locked(**k): raise RuntimeError("database is locked")
    monkeypatch.setattr(registry, "log_tool_call", locked)
    monkeypatch.setitem(registry.TOOL_DISPATCH, "ok_tool", lambda **k: {"success": True, "output": "did it"})
    r = registry.dispatch("ok_tool", {})
    assert r["success"] is True and r["output"] == "did it"


def test_provider_status_db_error_does_not_look_like_a_provider_failure(monkeypatch):
    monkeypatch.setattr(router, "update_provider_status", lambda *a: (_ for _ in ()).throw(RuntimeError("locked")))
    router._BREAKER.clear()
    router._status("p", True)            # must not raise
    assert "p" not in router._BREAKER


# ── harness keeps going / reports instead of dying ───────────────────────────

def test_a_crashing_tool_is_an_observation_and_the_task_continues(monkeypatch, tmp_path):
    def disp(name, args):
        if name == "flaky":
            raise RuntimeError("tool exploded")
        return {"success": True, "output": "fine"}

    res, _ = run_harness(monkeypatch, tmp_path, [call("flaky"), call("read_file", path="a"),
                                                 call("finish", success=True, summary="recovered")], dispatch=disp)
    assert res["status"] == "finished" and res["output"] == "recovered"


def test_unexpected_internal_error_returns_a_result_not_an_exception(monkeypatch, tmp_path):
    async def broken(*a, **k):
        raise RuntimeError("internal bug")

    monkeypatch.setattr(harness, "_execute_batch", broken)
    res, _ = run_harness(monkeypatch, tmp_path, [call("read_file", path="a")])
    assert res["status"] == "error" and "internal bug" in res["message"]


def test_tool_calls_run_in_the_session_project_directory(monkeypatch, tmp_path):
    seen = []
    proj = str(tmp_path / "proj"); os.makedirs(proj)

    def disp(name, args):
        seen.append((name, dict(args)))
        return {"success": True, "output": "ok"}

    run_harness(monkeypatch, tmp_path, [
        call("shell_exec", cmd="ls"), call("read_file", path="src/a.py"), call("read_file", path="/abs/b.py"),
        call("grep", pattern="x"), call("shell_exec", cmd="ls", cwd="/elsewhere"), call("finish", summary="d"),
    ], dispatch=disp, session_dir=proj)
    d = dict((i, a) for i, (_, a) in enumerate(seen))
    assert d[0]["cwd"] == proj
    assert d[1]["path"] == os.path.join(proj, "src/a.py") and d[2]["path"] == "/abs/b.py"
    assert d[3]["path"] == proj and d[4]["cwd"] == "/elsewhere"


def test_trace_write_failure_does_not_kill_the_task(tmp_path):
    import shutil
    tm = TaskManager("t", tasks_dir=str(tmp_path))
    shutil.rmtree(tm.dir)                # the directory vanishes (disk cleanup, unmounted drive...)
    tm.append_trace({"x": 1})            # must not raise


# ── shell_exec ───────────────────────────────────────────────────────────────

def test_shell_cwd_default_timeout_and_daemon_quoting(tmp_path, monkeypatch):
    from tools.system import shell_exec
    r = shell_exec(cmd="pwd", cwd=str(tmp_path))
    assert r["success"] and r["output"].strip() == str(tmp_path)
    assert not shell_exec(cmd="pwd", cwd=str(tmp_path / "missing"))["success"]
    # a command with both quote types and $ must reach bash unmangled when run as a daemon
    out = tmp_path / "out.txt"
    r = shell_exec(cmd=f"""echo "it's $((1+1))" > {out}; sleep 6""", is_daemon=True, cwd=str(tmp_path))
    assert r["success"]
    import time; time.sleep(1.0)
    assert out.read_text().strip() == "it's 2"


def test_shell_timeout_default_is_long_enough_for_builds(monkeypatch):
    import inspect
    from tools import system
    src = inspect.getsource(system.shell_exec)
    assert 'HARVIS_SHELL_TIMEOUT", "120"' in src and '"HARVIS_SHELL_MAX_TIMEOUT", "1800"' in src


# ── security defaults ────────────────────────────────────────────────────────

def test_telegram_bot_fails_closed_without_an_allowlist(monkeypatch):
    pytest.importorskip("telegram")
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "t")
    from telegram_bot import bot
    monkeypatch.setattr(bot, "TELEGRAM_ALLOWED_USERS", [])
    assert bot.is_user_allowed(123, "anyone") is False


# ── prompt growth: the cause of response time getting longer every turn ──────

def _chat(n, size):
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"question {i}"})
        out.append({"role": "assistant", "content": f"answer {i} " + "x" * size})
    return out


def test_earlier_chat_context_is_capped_and_keeps_newest_detail():
    prefix = _chat(10, 8000)                                  # 20 messages, ~80k chars
    out = harness._trim_prefix(prefix, 8000)
    total = sum(len(m["content"]) for m in out)
    assert total <= 8000 + 3000                               # budget (+ at most the newest message's overshoot)
    assert out[-1]["content"].startswith("answer 9") and "trimmed" in out[-1]["content"]
    assert out[0]["role"] == "user"                           # providers want a user turn first
    assert harness._trim_prefix(prefix, 0) == prefix          # 0 = unlimited, the old behaviour


def test_prompt_does_not_grow_with_conversation_length(monkeypatch, tmp_path):
    sizes = []
    for turns in (1, 3, 8, 10):
        seen = []

        def engine(history, **k):
            seen.append(harness._history_chars(history))
            return {"type": "text", "content": "ok"}

        monkeypatch.setattr(harness, "dispatch", lambda n, a: {"success": True, "output": "ok"})
        r = harness.HarnessRunner(task_id=f"t{turns}", engine=engine, history_prefix=_chat(turns, 8000))
        r.task_mgr = TaskManager(f"t{turns}", tasks_dir=str(tmp_path))
        asyncio.run(r.run("goal"))
        sizes.append(seen[0])
    assert sizes[-1] < sizes[1] * 1.6                         # was ~4x (and rising) before the cap
    assert sizes[-1] < 16000


def test_a_big_chat_history_no_longer_evicts_the_agents_own_steps(monkeypatch, tmp_path):
    """The window budget used to include the chat prefix, so a long chat left room for ONE step."""
    monkeypatch.setattr(harness.settings, "CONTEXT_CHARS", 12000)
    monkeypatch.setattr(harness.settings, "OLD_OBS_CHARS", 400)
    lens, it = [], iter([call("read_file", path=f"f{i}") for i in range(8)] + [{"type": "text", "content": "done"}])

    def engine(history, **k):
        lens.append(len(history))
        return next(it)

    monkeypatch.setattr(harness, "dispatch", lambda n, a: {"success": True, "output": "y" * 2500})
    r = harness.HarnessRunner(task_id="t", engine=engine, history_prefix=_chat(10, 8000))
    r.task_mgr = TaskManager("t", tasks_dir=str(tmp_path))
    asyncio.run(r.run("goal"))
    assert lens[-1] >= lens[3] + 6, f"agent lost its own steps: {lens}"


def test_only_the_newest_tool_outputs_stay_in_full():
    big = "z" * 3000 + "\n[harness: step 1, success=True]"
    h = [{"role": "user", "content": "goal"}]
    for i in range(5):
        h += [{"role": "assistant", "content": f"call {i}"}, {"role": "tool", "content": big}]
    harness._mask_old_observations(h, 0, keep_recent=2, keep_chars=400)
    tools = [m["content"] for m in h if m["role"] == "tool"]
    assert all(len(t) > 3000 for t in tools[-2:])
    assert all(len(t) < 700 and "trace.jsonl" in t and t.endswith("[harness: step 1, success=True]") for t in tools[:-2])
    before = [dict(m) for m in h]
    harness._mask_old_observations(h, 0, keep_recent=2, keep_chars=400)
    assert h == before                                        # idempotent


def test_tool_schemas_can_be_trimmed_per_deployment(monkeypatch):
    os.environ.setdefault("OPUS_PROXY", "x")
    from brain.providers import get_openai_tools
    names = [t["function"]["name"] for t in get_openai_tools()]
    monkeypatch.setenv("HARVIS_TOOLS_EXCLUDE", f"{names[0]}, {names[1]}")
    assert [t["function"]["name"] for t in get_openai_tools()] == names[2:]


def test_every_model_call_records_prompt_size_and_duration(monkeypatch, tmp_path):
    res, r = run_harness(monkeypatch, tmp_path, [call("read_file", path="a"), {"type": "text", "content": "ok"}])
    import json
    recs = [json.loads(l) for l in open(os.path.join(r.task_mgr.dir, "trace.jsonl")) if '"model_call"' in l]
    assert len(recs) == 2 and all(x["prompt_chars"] > 0 and "seconds" in x and x["ok"] for x in recs)
    assert recs[1]["prompt_chars"] > recs[0]["prompt_chars"]


def test_task_file_block_is_capped_so_it_cannot_regrow_the_prompt(tmp_path):
    tm = TaskManager("t", tasks_dir=str(tmp_path))
    tm.update_plan("# Goal\n" + "- [ ] step\n" * 5000)
    block = harness._task_block(tm)
    assert len(block) < 6500 and "trimmed" in block
