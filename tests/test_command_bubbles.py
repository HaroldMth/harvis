"""Dual-layer bubbles: a task bubble (the agent's checklist) + one live bubble per command HARVIS runs.
Command bubbles stay while the task runs and collapse only after the final answer."""
import asyncio
import os
import sys
import time
import types

import pytest

pytest.importorskip("telegram")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain.harness as harness  # noqa: E402
import telegram_bot.tg_io as tio  # noqa: E402
import telegram_bot.tg_ui as ui  # noqa: E402
import test_telegram_wiring as W  # noqa: E402
from test_telegram_wiring import env  # noqa: E402,F401  (fixture)
from test_telegram_format import problems  # noqa: E402
from brain.state_manager import TaskManager  # noqa: E402
from tools import registry  # noqa: E402

bot = W.bot
call = W.call
PLAN = call("update_task", content="# Goal\n- [x] setup\n- [ ] build it\n- [ ] test it")


class FakeBot:
    def __init__(self, tg):
        self.tg = tg

    async def delete_messages(self, chat_id, message_ids):
        self.tg.events.append({"kind": "delete_many", "ids": list(message_ids), "text": "", "mode": None})
        return True

    async def delete_message(self, chat_id, message_id):
        self.tg.events.append({"kind": "delete_one", "ids": [message_id], "text": "", "mode": None})
        return True


def make(tg):
    u = W.make_update(tg)
    u.effective_chat = types.SimpleNamespace(id=1, bot=FakeBot(tg))
    return u


def drive(script, env, monkeypatch, tg=None, real_tools=False, gap=0.0):
    tg = tg or W.FakeTG()
    it = iter(script)

    def fake_complete(history, **k):
        try:
            return next(it)
        except StopIteration:
            raise RuntimeError("scripted model ran out of replies")

    monkeypatch.setattr(W.router, "complete", fake_complete)
    monkeypatch.setattr(tio, "get_io", lambda cid: tio.ChatIO(gap=gap, margin=0.0))
    if real_tools:
        monkeypatch.setattr(harness, "dispatch", registry.dispatch)
    asyncio.run(bot.process_chat_query(make(tg), None, "build it"))
    return tg


# ── LiveBubble ───────────────────────────────────────────────────────────────

def test_live_bubble_applies_the_newest_state_even_if_updated_before_it_was_sent():
    async def go():
        tg = W.FakeTG()
        io = tio.ChatIO(gap=0.0, margin=0.0)
        b = tio.LiveBubble(io, W.Msg(tg), "first")
        b.update("second")
        b.update("third", final=True)
        b.update("late live update that must be ignored")
        await b.wait()
        await io.drain()
        assert b.message_id is not None
        shown = [e["text"] for e in tg.events if e["kind"] in ("send", "edit")]
        assert shown[-1] == "third" and "late live update that must be ignored" not in shown
    asyncio.run(go())


def test_live_bubble_never_blocks_the_caller():
    async def go():
        tg = W.FakeTG()
        io = tio.ChatIO(gap=0.5, margin=0.0)             # a slow chat: the send takes a while
        t = time.monotonic()
        b = tio.LiveBubble(io, W.Msg(tg), "x")
        for i in range(50):
            b.update(f"v{i}")
        assert time.monotonic() - t < 0.1                 # returned immediately
        await b.wait()
    asyncio.run(go())


# ── what the user sees ───────────────────────────────────────────────────────

def test_one_bubble_per_command_then_collapse_only_after_the_final_text(env, monkeypatch):
    tg = drive([PLAN, call("shell_exec", cmd="echo one"), call("shell_exec", cmd="echo two"),
                call("update_task", content="# Goal\n- [x] setup\n- [x] build it\n- [x] test it"),
                call("finish", success=True, summary="All built.")], env, monkeypatch)
    ev = tg.events
    cmd_sends = [e for e in tg.sends() if "$ echo" in e["text"]]
    assert [("echo one" in e["text"], "echo two" in e["text"]) for e in cmd_sends] == [(True, False), (False, True)]
    final_i = next(i for i, e in enumerate(ev) if e["kind"] == "send" and "All built." in e["text"])
    cmd_ids = {i for i, e in enumerate(ev) if e["kind"] == "send" and "$ echo" in e["text"]}
    assert max(cmd_ids) < final_i                                       # bubbles existed BEFORE the final text
    deletes = [i for i, e in enumerate(ev) if e["kind"] in ("delete_many", "delete_one")]
    assert deletes and min(deletes) > final_i                           # ...and collapse only AFTER it
    sealed = [e for e in ev if e["kind"] == "edit" and "Finished" in e["text"]]
    assert sealed and "3/3" in sealed[-1]["text"] and "2 commands" in sealed[-1]["text"]
    assert "<blockquote expandable>" in sealed[-1]["text"] and "$ echo one" in sealed[-1]["text"]
    assert not problems(sealed[-1]["text"])
    assert not [e for e in ev if e["kind"] == "delete" and e["id"] == 1]    # the task bubble is NOT deleted


def test_non_command_tools_get_no_bubble(env, monkeypatch):
    tg = drive([call("read_file", path="a"), call("grep", pattern="x"), call("finish", success=True, summary="ok")],
               env, monkeypatch)
    assert not [e for e in tg.sends() if "$ " in e["text"]]


def test_a_plain_chat_reply_leaves_nothing_behind(env, monkeypatch):
    tg = drive([{"type": "text", "content": "Hello there"}], env, monkeypatch)
    assert tg.finals("Hello there") and not [e for e in tg.events if e["kind"] == "edit" and "Finished" in e["text"]]
    assert tg.events[-1]["kind"] == "delete"                              # the status bubble was removed


def test_an_abnormal_end_keeps_the_command_bubbles_as_evidence(env, monkeypatch):
    from brain import settings
    monkeypatch.setattr(settings, "MAX_STEPS", 3)
    tg = drive([call("shell_exec", cmd=f"echo {i}") for i in range(6)], env, monkeypatch)
    assert tg.finals("continue")
    assert not [e for e in tg.events if e["kind"] in ("delete_many", "delete_one")]      # nothing collapsed
    assert any("Stopped early" in e["text"] for e in tg.events if e["kind"] == "edit")


def test_live_output_streams_into_the_command_bubble_while_it_runs(env, monkeypatch):
    tg = drive([call("shell_exec", cmd="for i in 1 2 3; do echo line$i; sleep 0.7; done"),
                call("finish", success=True, summary="ok")], env, monkeypatch, real_tools=True, gap=0.05)
    live = [e for e in tg.events if e["kind"] == "edit" and "Running" in e["text"] and "line1" in e["text"]]
    assert live, "no live edit showed partial output"


def test_a_failed_command_bubble_shows_the_exit_code_and_stays_readable(env, monkeypatch):
    from brain import settings
    monkeypatch.setattr(settings, "MAX_STEPS", 2)       # abnormal end so the bubble is not collapsed away
    tg = drive([call("shell_exec", cmd="echo boom >&2; exit 7"), call("shell_exec", cmd="echo again >&2; exit 7")],
               env, monkeypatch, real_tools=True)
    edits = [e["text"] for e in tg.events if e["kind"] == "edit" and "(exit 7)" in e["text"]]   # the badge, not the command text
    first = next(t for t in edits if "boom" in t)
    assert "Failed in" in first and "[stderr/error]" not in first and not problems(first)


def test_stop_kills_the_running_command_not_just_the_next_step(env, monkeypatch):
    pidfile = os.path.join(env_dir := os.path.dirname(__file__), "_stop_pid")
    if os.path.exists(pidfile):
        os.remove(pidfile)
    tg = W.FakeTG()
    it = iter([call("shell_exec", cmd=f"echo $$ > {pidfile}; exec sleep 40"), call("finish", success=True, summary="x")])
    monkeypatch.setattr(W.router, "complete", lambda h, **k: next(it))
    monkeypatch.setattr(tio, "get_io", lambda cid: tio.ChatIO(gap=0.0, margin=0.0))
    monkeypatch.setattr(harness, "dispatch", registry.dispatch)

    async def go():
        task = asyncio.ensure_future(bot.process_chat_query(make(tg), None, "go"))
        await asyncio.sleep(1.2)
        bot.ACTIVE_CANCELS[1].set()
        t = time.time()
        await asyncio.wait_for(task, 10)
        return time.time() - t
    took = asyncio.run(go())
    try:
        pid = int(open(pidfile).read())
        time.sleep(0.2)
        alive = os.path.exists(f"/proc/{pid}") and open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[0] != "Z"
        assert took < 6 and not alive
        assert tg.finals("stopped")
    finally:
        if os.path.exists(pidfile):
            os.remove(pidfile)


# ── harness event contract ───────────────────────────────────────────────────

def test_harness_events_tie_acting_output_and_checked_together_by_call_id(monkeypatch, tmp_path):
    events = []
    it = iter([call("shell_exec", cmd="for i in 1 2; do echo l$i; sleep 0.7; done"), {"type": "text", "content": "done"}])
    monkeypatch.setattr(harness, "dispatch", registry.dispatch)
    r = harness.HarnessRunner(task_id="t", engine=lambda h, **k: next(it))
    r.task_mgr = TaskManager("t", tasks_dir=str(tmp_path))
    asyncio.run(r.run("goal", progress_callback=events.append))
    acting = [e for e in events if e["type"] == "acting"]
    outputs = [e for e in events if e["type"] == "output"]
    checked = [e for e in events if e["type"] == "checked"]
    assert len(acting) == len(checked) == 1 and acting[0]["call_id"] == checked[0]["call_id"] == "1.0"
    assert outputs and all(o["call_id"] == "1.0" for o in outputs)
    assert checked[0]["exit_code"] == 0 and "l2" in checked[0]["output"]


def test_dispatch_keeps_exit_code_timeout_and_cancel_flags():
    r = registry.dispatch("shell_exec", {"cmd": "exit 2"})
    assert r["success"] is False and r["code"] == 2 and r["status"] == "failed"
    r = registry.dispatch("shell_exec", {"cmd": "echo partial; sleep 30", "timeout": 3})
    assert r["success"] is True and r["timed_out"] is True and "partial" in r["output"]
    import threading
    ev = threading.Event(); threading.Timer(0.5, ev.set).start()
    from tools.system import STREAM_HOOKS
    token = STREAM_HOOKS.set({"cancel_event": ev})
    try:
        r = registry.dispatch("shell_exec", {"cmd": "sleep 30"})
    finally:
        STREAM_HOOKS.reset(token)
    assert r["cancelled"] is True and r["success"] is False


def test_the_harness_reports_the_real_exit_code_and_a_cancelled_command_as_stopped(monkeypatch, tmp_path):
    events = []
    it = iter([call("shell_exec", cmd="echo x; exit 4"), {"type": "text", "content": "ok"}])
    monkeypatch.setattr(harness, "dispatch", registry.dispatch)
    r = harness.HarnessRunner(task_id="t", engine=lambda h, **k: next(it))
    r.task_mgr = TaskManager("t", tasks_dir=str(tmp_path))
    asyncio.run(r.run("goal", progress_callback=events.append))
    assert [e["exit_code"] for e in events if e["type"] == "checked"] == [4]


def test_hooks_are_invisible_to_the_model_and_to_the_tool_log(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(harness, "dispatch", lambda n, a: seen.append(dict(a)) or {"success": True, "output": "ok"})
    it = iter([call("shell_exec", cmd="ls"), {"type": "text", "content": "k"}])
    r = harness.HarnessRunner(task_id="t", engine=lambda h, **k: next(it))
    r.task_mgr = TaskManager("t", tasks_dir=str(tmp_path))
    asyncio.run(r.run("goal"))
    assert seen == [{"cmd": "ls"}]                    # no callbacks smuggled into the arguments


# ── rendering ────────────────────────────────────────────────────────────────

def test_checklist_folds_completed_items_while_running_and_lists_all_when_final():
    plan = "\n".join([f"- [x] done {i}" for i in range(5)] + ["- [ ] current <one>", "- [ ] next"] + [f"- [ ] later {i}" for i in range(8)])
    lines, done, total = ui.checklist_lines(plan)
    text = "\n".join(lines)
    assert (done, total) == (5, 15) and "[x] <i>5 completed</i>" in text and "[⏳] <b>current &lt;one&gt;</b>" in text and "[ ] <i>next</i>" in text and "+4 more" in text
    flines, _, _ = ui.checklist_lines(plan, final=True)
    assert flines[0].startswith("[x] ") and flines[-3].startswith("[ ] ") and len(flines) == 15 and "✅" not in "".join(flines) and not problems("\n".join(flines))


def test_task_bubble_shows_the_running_command_and_hides_the_step_list_when_a_plan_exists():
    st = ui.Status("Default", "🛡️")
    st.set_plan("- [x] a\n- [ ] b")
    st.start(1, "shell_exec", {"cmd": "npm test"})
    out = st.render("Thinking…")
    assert "⚡ <code>$ npm test</code>" in out and "[█████░░░░░] 50%" in out and "[x] a" in out and "[⏳] <b>b</b>" in out and not problems(out)
    st2 = ui.Status("Default", "🛡️")
    st2.start(1, "shell_exec", {"cmd": "ls"})
    assert "✅" not in st2.render() and "⏳ <code>$ ls</code>" in st2.render()      # no plan: steps listed instead


def test_sealed_task_bubble_is_valid_html_for_hostile_commands_and_many_of_them():
    st = ui.Status("Default", "🛡️")
    st.set_plan("- [x] a\n- [ ] <b>b</b>")
    for i in range(60):
        st.start(i, "shell_exec", {"cmd": f"echo '<{i}>' && cat 'a&b' | grep \"x\""})
        st.finish(i, i % 5 != 0, "shell_exec", "line\nERROR <boom> & fail" if i % 5 == 0 else "ok")
    for outcome in ("done", "stopped", "failed"):
        out = st.render_final(outcome)
        assert not problems(out) and "+35 earlier commands" in out and "<blockquote expandable>" in out
