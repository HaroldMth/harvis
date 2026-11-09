"""Telegram handler -> HarnessRunner -> fake model/tools, against a fake Telegram that can
inject flood control and parse errors."""
import asyncio
import os
import sys
import threading
import types

import pytest

pytest.importorskip("telegram")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

bot = pytest.importorskip("telegram_bot.bot")
import brain.harness as harness  # noqa: E402
import brain.router as router  # noqa: E402
import telegram_bot.tg_io as tio  # noqa: E402
from brain.state_manager import TaskManager  # noqa: E402
from telegram.error import BadRequest, RetryAfter  # noqa: E402


class FakeTG:
    """Records every API call. `faults` maps call number -> exception to raise on that call."""

    def __init__(self, faults=None):
        self.events, self.faults, self.n, self.ids = [], dict(faults or {}), 0, 0

    def new_id(self):
        self.ids += 1
        return self.ids

    def _hit(self, kind, mid, text, mode):
        self.n += 1
        self.events.append({"kind": kind, "id": mid, "text": text, "mode": mode})
        fault = self.faults.pop(self.n, None)
        if fault:
            raise fault

    # convenience views
    def sends(self):
        return [e for e in self.events if e["kind"] == "send"]

    def finals(self, needle):
        return [e for e in self.sends() if needle in e["text"]]


class Msg:
    def __init__(self, tg, text="", chat_id=1):
        self.tg, self.text, self.chat_id = tg, text, chat_id
        self.message_id = tg.new_id()
        self.reply_to_message = None
        self.deleted = False

    async def reply_text(self, text, parse_mode=None, reply_markup=None, **kw):
        self.tg._hit("send", None, text, parse_mode)
        m = Msg(self.tg, text, self.chat_id)
        m.markup = reply_markup
        return m

    async def edit_text(self, text, parse_mode=None, reply_markup=None, **kw):
        self.tg._hit("edit", self.message_id, text, parse_mode)

    async def delete(self):
        self.tg._hit("delete", self.message_id, "", None)
        self.deleted = True

    async def reply_document(self, doc, caption=None, **kw):
        self.tg._hit("document", None, caption or "", None)
        return Msg(self.tg, "", self.chat_id)


def make_update(tg, uid=1):
    u = types.SimpleNamespace()
    u.effective_user = types.SimpleNamespace(id=uid, username="h")
    u.message = Msg(tg)
    return u


@pytest.fixture
def env(monkeypatch, tmp_path):
    saved = []
    monkeypatch.setattr(bot, "get_active_session", lambda uid: "general")
    monkeypatch.setattr(bot, "get_session_info", lambda sid: None)
    monkeypatch.setattr(bot, "update_session", lambda *a, **k: None)
    monkeypatch.setattr(bot, "get_history", lambda sid, limit=20: [
        {"role": "user", "content": "earlier q"},
        {"role": "assistant", "content": '{"type": "tool_call", "tool_name": "x"}'},  # legacy plumbing, dropped
        {"role": "assistant", "content": "earlier a"},
    ])
    monkeypatch.setattr(bot, "save_message", lambda sid, role, c: saved.append((role, c)))
    monkeypatch.setattr(bot, "set_harvis_busy", lambda b: None)
    monkeypatch.setattr(harness, "dispatch", lambda n, a: {"success": True, "output": f"ran {a.get('cmd')}"})
    monkeypatch.setattr(tio, "get_io", lambda cid: tio.ChatIO(gap=0.0, margin=0.0))
    bot.RUNNING.clear(); bot.QUEUES.clear(); bot.ACTIVE_CANCELS.clear()

    class TM(TaskManager):
        def __init__(self, tid, **kw):
            super().__init__(tid, tasks_dir=str(tmp_path))

    monkeypatch.setattr(harness, "TaskManager", TM)
    return saved


def call(name, **a):
    return {"type": "tool_call", "tool_name": name, "tool_args": a, "content": None}


def run(script, env, monkeypatch, text="do it", tg=None, via="process"):
    tg = tg or FakeTG()
    seen, it = [], iter(script)

    def fake_complete(history, **k):
        seen.append([dict(m) for m in history])
        try:
            return next(it)
        except StopIteration:
            raise RuntimeError("scripted model ran out of replies")

    monkeypatch.setattr(router, "complete", fake_complete)
    update = make_update(tg)
    fn = bot.process_chat_query if via == "process" else bot.submit_query
    asyncio.run(fn(update, None, text))
    return tg, seen


def test_multistep_task_delivers_summary_and_cleans_up(env, monkeypatch):
    tg, seen = run([call("shell_exec", cmd="ls"), call("finish", success=True, summary="All done, **Harold**!")],
                   env, monkeypatch)
    final = tg.finals("All done")
    assert len(final) == 1 and "<b>Harold</b>" in final[0]["text"] and final[0]["mode"] is not None
    assert tg.events[-1]["kind"] == "delete"                       # status bubble removed after delivery
    flat = [str(m["content"]) for m in seen[0]]
    assert "earlier q" in flat and "earlier a" in flat and not any("tool_call" in f for f in flat)
    assert env[0] == ("user", "[via Telegram] do it") and env[-1][0] == "assistant"


def test_simple_chat_is_one_turn_no_tools(env, monkeypatch):
    tg, seen = run([{"type": "text", "content": "Hey Harold!"}], env, monkeypatch, text="hi")
    assert len(seen) == 1 and tg.finals("Hey Harold!")


def test_budget_stop_tells_user_how_to_resume_and_saves_trace_pointer(env, monkeypatch):
    from brain import settings
    monkeypatch.setattr(settings, "MAX_STEPS", 2)
    tg, _ = run([call("shell_exec", cmd=str(i)) for i in range(5)], env, monkeypatch)
    assert tg.finals("continue")
    assert "trace.jsonl" in env[-1][1]


def test_many_steps_cost_few_api_calls(env, monkeypatch):
    """The agent must not generate Telegram traffic per step: 25 tool steps => a handful of calls."""
    monkeypatch.setattr(tio, "get_io", lambda cid: tio.ChatIO(gap=0.05, margin=0.0))
    script = [call("shell_exec", cmd=f"ls {i}") for i in range(25)] + [call("finish", success=True, summary="done")]
    tg, _ = run(script, env, monkeypatch)
    assert tg.n <= 10, f"{tg.n} Telegram API calls for a 25-step run"


def test_flood_control_delays_but_never_loses_or_duplicates_the_answer(env, monkeypatch):
    # every one of the first 4 calls is rejected with flood control
    faults = {i: RetryAfter(0.01) for i in range(1, 5)}
    tg, _ = run([{"type": "text", "content": "The answer"}], env, monkeypatch, tg=FakeTG(faults))
    assert len(tg.finals("The answer")) >= 1
    delivered = [e for e in tg.finals("The answer")]
    # exactly one successful delivery: failed attempts are retries of the same call, not new messages
    ok_sends = [e for e in tg.sends() if "The answer" in e["text"]]
    assert len(ok_sends) == len(delivered) and len(ok_sends) <= 1 + len(faults)


def test_parse_error_falls_back_to_plain_text_once(env, monkeypatch):
    faults = {}
    tg = FakeTG(faults)
    orig = tg._hit

    def hit(kind, mid, text, mode):
        if kind == "send" and mode is not None and "Answer" in text and not getattr(hit, "done", False):
            hit.done = True
            tg.n += 1
            tg.events.append({"kind": kind, "id": mid, "text": text, "mode": mode})
            raise BadRequest("Can't parse entities: can't find end of the entity")
        return orig(kind, mid, text, mode)

    tg._hit = hit
    run([{"type": "text", "content": "Answer"}], env, monkeypatch, tg=tg)
    plain = [e for e in tg.sends() if "Answer" in e["text"] and e["mode"] is None]
    assert len(plain) == 1


def test_message_during_a_running_task_is_queued_not_cancelling_it(env, monkeypatch):
    tg = FakeTG()
    order = []
    scripts = {"first": iter([call("shell_exec", cmd="ls a"), call("finish", success=True, summary="FIRST done")]),
               "second": iter([{"type": "text", "content": "SECOND done"}])}
    state = {"cur": "first", "calls": 0}

    async def go():
        u1, u2 = make_update(tg), make_update(tg)
        gate = asyncio.Event()

        def fake_complete(history, **k):
            state["calls"] += 1
            if state["calls"] == 1:
                # while the first task is mid-flight, a second message arrives
                fut = asyncio.run_coroutine_threadsafe(bot.submit_query(u2, None, "side question"), loop)
                fut.result(timeout=5)
            order.append(state["cur"])
            if state["calls"] >= 3:
                state["cur"] = "second"
            return next(scripts["first"] if state["calls"] <= 2 else scripts["second"])

        monkeypatch.setattr(router, "complete", fake_complete)
        loop = asyncio.get_running_loop()
        await bot.submit_query(u1, None, "long job")

    asyncio.run(go())
    assert tg.finals("Queued")                       # user told it is queued
    assert tg.finals("FIRST done")                   # first task was NOT cancelled
    assert tg.finals("SECOND done")                  # queued message ran afterwards
    texts = [e["text"] for e in tg.sends() if "done" in e["text"]]
    assert texts.index(next(t for t in texts if "FIRST" in t)) < texts.index(next(t for t in texts if "SECOND" in t))


def test_clean_response_keeps_real_javascript_but_drops_react_ui():
    js = "Here:\n```js\nconst a = [1,2].map(x => x * 2);\nconsole.log(a);\n```\nDone"
    assert "console.log" in bot.clean_response(js)
    ui_blob = "Intro\n```jsx\nimport React from 'react';\nexport default function A(){return <div className='x'/>}\n```"
    assert "export default" not in bot.clean_response(ui_blob)
