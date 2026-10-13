"""End-to-end smoke test: Telegram handler -> HarnessRunner -> fake model/tools."""
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
from brain.state_manager import TaskManager  # noqa: E402


class Msg:
    def __init__(self, log, text=""):
        self.log, self.text, self.deleted = log, text, False
        self.reply_to_message = None

    async def reply_text(self, text, **kw):
        self.log.append(("reply", text))
        return Msg(self.log, text)

    async def edit_text(self, text, **kw):
        self.log.append(("edit", text))

    async def delete(self):
        self.deleted = True


def make_update(log):
    u = types.SimpleNamespace()
    u.effective_user = types.SimpleNamespace(id=1, username="h")
    u.message = Msg(log)
    return u


@pytest.fixture
def env(monkeypatch, tmp_path):
    saved = []
    monkeypatch.setattr(bot, "get_history", lambda sid, limit=20: [
        {"role": "user", "content": "earlier q"},
        {"role": "assistant", "content": '{"type": "tool_call", "tool_name": "x"}'},  # legacy plumbing, must be dropped
        {"role": "assistant", "content": "earlier a"},
    ])
    monkeypatch.setattr(bot, "save_message", lambda sid, role, c: saved.append((role, c)))
    monkeypatch.setattr(bot, "set_harvis_busy", lambda b: None)
    monkeypatch.setattr(harness, "dispatch", lambda n, a: {"success": True, "output": f"ran {a.get('cmd')}"})

    class TM(TaskManager):
        def __init__(self, tid):
            super().__init__(tid, tasks_dir=str(tmp_path))

    monkeypatch.setattr(harness, "TaskManager", TM)
    return saved


def run(script, env, monkeypatch, text="do it"):
    seen = []
    it = iter(script)

    def fake_complete(history, **k):
        seen.append([dict(m) for m in history])
        return next(it)

    monkeypatch.setattr(router, "complete", fake_complete)
    log = []
    update = make_update(log)
    asyncio.run(bot.process_chat_query(update, None, text))
    return log, seen


def call(name, **a):
    return {"type": "tool_call", "tool_name": name, "tool_args": a, "content": None}


def test_multistep_task_streams_progress_and_delivers_agent_summary(env, monkeypatch):
    log, seen = run([call("shell_exec", cmd="ls"), call("finish", success=True, summary="All done, Harold!")],
                    env, monkeypatch)
    texts = [t for _, t in log]
    assert any("shell_exec" in t and "✅" in t for t in texts)          # step bubble updated
    assert texts[-1] == "All done, Harold!"                                # agent's own words delivered
    # chat memory reached the model, legacy tool-call junk did not
    flat = [str(m["content"]) for m in seen[0]]
    assert "earlier q" in flat and "earlier a" in flat and not any("tool_call" in f for f in flat)
    assert env[0] == ("user", "[via Telegram] do it") and env[-1] == ("assistant", "All done, Harold!")


def test_simple_chat_is_one_turn_no_tools(env, monkeypatch):
    log, seen = run([{"type": "text", "content": "Hey Harold!"}], env, monkeypatch, text="hi")
    assert len(seen) == 1 and log[-1][1] == "Hey Harold!"


def test_budget_stop_tells_user_how_to_resume_and_saves_trace_pointer(env, monkeypatch):
    from brain import settings
    monkeypatch.setattr(settings, "MAX_STEPS", 2)
    log, _ = run([call("shell_exec", cmd=str(i)) for i in range(5)], env, monkeypatch)
    assert "continue" in log[-1][1].lower()
    assert "trace.jsonl" in env[-1][1]
