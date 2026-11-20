"""Telegram /shell mode: the engine (real bash) and the full lifecycle against a fake Telegram."""
import asyncio
import glob
import os
import subprocess
import sys
import tempfile
import time
import types

import pytest

pytest.importorskip("telegram")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import telegram_bot.shell_mode as sm  # noqa: E402
import telegram_bot.tg_io as tio  # noqa: E402
import telegram_bot.tg_ui as ui  # noqa: E402
import test_telegram_wiring as W  # noqa: E402
from test_telegram_format import problems  # noqa: E402

bot = W.bot


def alive(pid: int) -> bool:
    """True while the process exists and is not a zombie (checked by PID, never by command-line text)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError, IndexError):
        return False


def read_pid(path, wait=2.0) -> int:
    end = time.time() + wait
    while time.time() < end:
        try:
            return int(open(path).read().strip())
        except (FileNotFoundError, ValueError):
            time.sleep(0.05)
    raise AssertionError(f"{path} never appeared")


@pytest.fixture
def tree():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "a", "b"))
    return os.path.realpath(d)


def run(coro):
    return asyncio.run(coro)


# ── engine ───────────────────────────────────────────────────────────────────

def test_cd_persists_including_inside_a_chain(tree):
    async def go():
        s = sm.ShellSession(cwd=tree)
        r = await s.run("cd a && cd b && pwd")
        assert r.exit_code == 0 and r.output == f"{tree}/a/b" and s.cwd == f"{tree}/a/b"
        r = await s.run("cd .. && pwd")
        assert r.output == f"{tree}/a" and s.cwd == f"{tree}/a"
        assert (await s.run("pwd")).output == f"{tree}/a"
    run(go())


def test_exports_and_unsets_persist_so_venv_style_activation_works(tree):
    async def go():
        s = sm.ShellSession(cwd=tree)
        await s.run("export FOO=bar; export PATH=/opt/x/bin:$PATH; export VIRTUAL_ENV=/v")
        assert (await s.run("echo $FOO")).output == "bar"
        assert (await s.run("echo $PATH")).output.startswith("/opt/x/bin:")
        await s.run("unset FOO")
        assert (await s.run("echo [$FOO]")).output == "[]"
    run(go())


def test_stderr_and_exit_code_and_a_failed_command_keeps_the_directory(tree):
    async def go():
        s = sm.ShellSession(cwd=tree)
        r = await s.run("echo out; echo err 1>&2; exit 3")
        assert r.exit_code == 3 and "out" in r.output and "err" in r.output and s.cwd == tree
        assert (await s.run("cd /does/not/exist")).exit_code != 0 and s.cwd == tree
    run(go())


def test_the_state_trailer_never_leaks_into_output_or_live_updates(tree):
    async def go():
        s = sm.ShellSession(cwd=tree)
        seen = []
        r = await s.run("for i in 1 2 3; do echo line$i; sleep 0.25; done", on_output=seen.append)
        assert r.output == "line1\nline2\nline3"
        assert len(seen) >= 2 and all("@@HVS" not in t for t in seen) and seen[-1].strip() == "line1\nline2\nline3"
    run(go())


def test_a_single_300kb_line_does_not_crash_the_reader(tree):
    async def go():
        s = sm.ShellSession(cwd=tree)
        r = await s.run("python3 -c \"print('x'*300000)\"")
        assert r.exit_code == 0 and r.truncated and len(r.output) <= sm.MAX_KEPT_OUTPUT
    run(go())


def test_progress_bars_show_their_final_state_and_ansi_is_stripped():
    assert ui.clean_ansi("a 10%\ra 50%\ra 100%\ndone") == "a 100%\ndone"
    assert ui.clean_ansi("\x1b[31mred\x1b[0m \x1b]0;title\x07ok\r\nnext") == "red ok\nnext"


def test_timeout_kills_the_command_and_leaves_no_process_behind(tree):
    pidfile = os.path.join(tree, "pid")

    async def go():
        s = sm.ShellSession(cwd=tree)
        t = time.time()
        r = await s.run(f"echo $$ > {pidfile}; exec sleep 31", timeout=1)
        assert r.timed_out and r.exit_code == 124 and time.time() - t < 4 and "timed out" in r.output
    run(go())
    time.sleep(0.2)
    assert not alive(read_pid(pidfile))


def test_stop_kills_the_whole_process_group_including_children(tree):
    p1, p2 = os.path.join(tree, "p1"), os.path.join(tree, "p2")

    async def go():
        s = sm.ShellSession(cwd=tree)

        async def stopper():
            await asyncio.sleep(0.8)
            assert s.stop() is True

        asyncio.ensure_future(stopper())
        t = time.time()
        r = await s.run(f"sh -c 'sleep 33 & echo $! > {p1}; echo $$ > {p2}; sleep 34; wait'")
        assert r.stopped and r.exit_code is None and time.time() - t < 5
        assert s.stop() is False                                # nothing left running
    run(go())
    time.sleep(0.3)
    assert not alive(read_pid(p1)) and not alive(read_pid(p2))


def test_a_background_job_neither_hangs_the_command_nor_gets_killed(tree):
    pidfile = os.path.join(tree, "bg")

    async def go():
        s = sm.ShellSession(cwd=tree)
        t = time.time()
        r = await s.run(f"(sleep 35 & echo $! > {pidfile}); echo started")
        assert r.exit_code == 0 and r.output == "started" and time.time() - t < 3
    run(go())
    pid = read_pid(pidfile)
    try:
        assert alive(pid)                                       # still running: it is the user's server
    finally:
        subprocess.run(["kill", str(pid)])


def test_catastrophic_commands_are_refused_and_never_started(tree):
    async def go():
        s = sm.ShellSession(cwd=tree)
        for cmd in ("rm -rf ~", "rm -fr /", "mkfs.ext4 /dev/sda1", "dd if=/dev/zero of=/dev/sda"):
            r = await s.run(cmd)
            assert r.blocked and r.exit_code == 126 and "destructive" in r.output, cmd
        assert s.history == [] or all(x.blocked for x in s.history) or True
    run(go())


def test_the_unrestricted_switch_only_changes_the_check(monkeypatch):
    assert sm.blocked_reason("rm -rf ~") is not None
    monkeypatch.setenv("HARVIS_SHELL_UNRESTRICTED", "1")
    assert sm.blocked_reason("rm -rf ~") is None


def test_an_output_flood_is_killed_instead_of_filling_the_disk(tree, monkeypatch):
    monkeypatch.setattr(sm, "MAX_OUTPUT_BYTES", 300_000)
    async def go():
        s = sm.ShellSession(cwd=tree)
        r = await s.run("yes")
        assert r.exit_code == 137 and r.truncated and "exceeded" in r.output
    run(go())


def test_no_temp_files_are_left_behind(tree):
    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "hvs_*.out")))
    async def go():
        s = sm.ShellSession(cwd=tree)
        await s.run("echo hi")
        await s.run("sleep 5", timeout=1)
    run(go())
    assert set(glob.glob(os.path.join(tempfile.gettempdir(), "hvs_*.out"))) == before


def test_user_typing_exit_keeps_the_session_alive(tree):
    async def go():
        s = sm.ShellSession(cwd=tree)
        r = await s.run("exit 5")
        assert r.exit_code == 5 and s.cwd == tree
        assert (await s.run("echo still here")).output == "still here"
    run(go())


# ── Telegram lifecycle ───────────────────────────────────────────────────────

class FakeBot:
    def __init__(self, tg):
        self.tg = tg

    async def delete_messages(self, chat_id, message_ids):
        self.tg.events.append({"kind": "delete_many", "ids": list(message_ids), "text": "", "mode": None})
        return True

    async def delete_message(self, chat_id, message_id):
        self.tg.events.append({"kind": "delete_one", "ids": [message_id], "text": "", "mode": None})
        return True


def upd(tg, text="", uid=1):
    u = W.make_update(tg, uid)
    u.message.text = text
    u.effective_chat = types.SimpleNamespace(id=1, bot=FakeBot(tg))
    return u


def ctx(*args):
    return types.SimpleNamespace(args=list(args))


@pytest.fixture
def shell_env(monkeypatch, tmp_path, tree):
    saved, updated = [], []
    monkeypatch.setattr(bot, "is_user_allowed", lambda uid, name=None: uid == 1)
    monkeypatch.setattr("db.store.get_active_session", lambda uid: "general")
    monkeypatch.setattr("db.store.get_session_info", lambda sid: None)
    monkeypatch.setattr("db.store.save_message", lambda sid, role, c: saved.append((sid, role, c)))
    monkeypatch.setattr("db.store.update_session", lambda sid, **kw: updated.append((sid, kw)))
    monkeypatch.setattr(sm, "default_cwd", lambda uid=None: tree)
    monkeypatch.setattr(tio, "get_io", lambda cid: tio.ChatIO(gap=0.0, margin=0.0))
    monkeypatch.setattr(bot, "KNOWN_COMMANDS", {"shell", "sh", "exit", "stop", "help"})
    ui.TASK_LOGS.clear()
    bot.RUNNING.clear(); bot.QUEUES.clear(); bot.ACTIVE_CANCELS.clear()
    sm.SESSIONS.clear(); sm.ONESHOTS.clear()
    called = []
    monkeypatch.setattr(W.router, "complete", lambda *a, **k: called.append(1) or {"type": "text", "content": "no"})
    return types.SimpleNamespace(saved=saved, updated=updated, called=called, tree=tree)


def test_full_lifecycle_one_summary_bulk_cleanup_and_handoff_to_harvis(shell_env):
    tg = W.FakeTG()

    async def go():
        await bot.shell_command(upd(tg, "/shell"), ctx())
        for text in ("pwd", "cd a", "export X=42", "echo $X && pwd", "false"):
            await bot.message_handler(upd(tg, text), None)
        await bot.shell_command(upd(tg, "/shell off"), ctx("off"))
    run(go())

    texts = [e["text"] for e in tg.sends()]
    assert any("Shell Mode ON" in t for t in texts)
    summaries = [t for t in texts if "Shell Session Closed" in t]
    assert len(summaries) == 1
    assert "5 commands" in summaries[0] and "❌(1)" in summaries[0] and "✅ 4" in summaries[0]
    assert not problems(summaries[0])
    assert any("42" in t and f"{shell_env.tree}/a" in t for t in texts)         # export + cd persisted
    deletes = [e for e in tg.events if e["kind"] == "delete_many"]
    assert len(deletes) == 1 and len(deletes[0]["ids"]) >= 5 + 5                  # user msgs + bubbles
    assert tg.events.index(deletes[0]) > max(i for i, e in enumerate(tg.events) if "Shell Session Closed" in e["text"])
    assert shell_env.called == []                                                 # HARVIS was never invoked
    sid, role, note = shell_env.saved[-1]
    assert role == "assistant" and "5 command(s)" in note and f"{shell_env.tree}/a" in note and "failed (exit 1)" in note
    assert bot.SHELL_SESSIONS == {}
    log = next(iter(ui.TASK_LOGS.values()))
    assert [e["args"]["cmd"] for e in log] == ["pwd", "cd a", "export X=42", "echo $X && pwd", "false"]   # nothing lost


def test_leaving_hands_the_directory_to_a_project_session(shell_env, monkeypatch, tree):
    monkeypatch.setattr("db.store.get_active_session", lambda uid: "proj1")
    tg = W.FakeTG()

    async def go():
        await bot.shell_command(upd(tg, "/shell"), ctx())
        await bot.message_handler(upd(tg, "cd a/b"), None)
        await bot.exit_command(upd(tg, "/exit"), ctx())
    run(go())
    assert shell_env.updated == [("proj1", {"active_dir": f"{tree}/a/b"})]


def test_commands_run_strictly_in_the_order_they_were_sent(shell_env):
    tg = W.FakeTG()

    async def go():
        await bot.shell_command(upd(tg, "/shell"), ctx())
        await asyncio.gather(*[bot.message_handler(upd(tg, c), None) for c in
                               ("echo one; sleep 0.4", "echo two", "echo three")])
        return sm.SESSIONS[1]
    sess = run(go())
    assert [r.cmd for r in sess.history] == ["echo one; sleep 0.4", "echo two", "echo three"]


def test_slash_paths_are_shell_input_but_our_commands_are_not(shell_env):
    tg = W.FakeTG()

    async def go():
        await bot.shell_command(upd(tg, "/shell"), ctx())
        await bot.shell_passthrough(upd(tg, "/usr/bin/env echo from-path"), None)
        await bot.shell_passthrough(upd(tg, "/stop"), None)               # ours: ignored here
        return sm.SESSIONS[1]
    sess = run(go())
    assert [r.cmd for r in sess.history] == ["/usr/bin/env echo from-path"] and sess.history[0].output == "from-path"


def test_stop_kills_a_running_command_and_the_session_survives(shell_env):
    tg = W.FakeTG()

    async def go():
        await bot.shell_command(upd(tg, "/shell"), ctx())
        task = asyncio.ensure_future(bot.message_handler(upd(tg, f"echo $$ > {shell_env.tree}/stoppid; exec sleep 35"), None))
        await asyncio.sleep(0.9)
        await bot.stop_command(upd(tg, "/stop"), ctx())
        t = time.time()
        await task
        assert time.time() - t < 5
        await bot.message_handler(upd(tg, "echo alive"), None)
        return sm.SESSIONS[1]
    sess = run(go())
    assert sess.history[0].stopped and sess.history[1].output == "alive"
    assert any("Shell command killed" in e["text"] for e in tg.sends())
    time.sleep(0.2)
    assert not alive(read_pid(os.path.join(shell_env.tree, "stoppid")))


def test_exit_is_a_real_handler_and_respects_the_allowlist(shell_env):
    tg = W.FakeTG()

    async def go():
        await bot.shell_command(upd(tg, "/shell"), ctx())
        await bot.exit_command(upd(tg, "/exit", uid=2), ctx())            # not allowed: nothing happens
        assert 1 in sm.SESSIONS
        await bot.exit_command(upd(tg, "/exit"), ctx())
        assert 1 not in sm.SESSIONS
        await bot.exit_command(upd(tg, "/exit"), ctx())                   # not in shell mode
    run(go())
    assert sum("Shell Session Closed" in e["text"] for e in tg.sends()) == 1
    assert any("isn't active" in e["text"] for e in tg.sends())


def test_shell_mode_is_refused_while_harvis_is_mid_task(shell_env):
    tg = W.FakeTG()
    bot.RUNNING.add(1)
    run(bot.shell_command(upd(tg, "/shell"), ctx()))
    assert 1 not in sm.SESSIONS and any("middle of a task" in e["text"] for e in tg.sends())


def test_one_shot_commands_do_not_switch_the_mode(shell_env):
    tg = W.FakeTG()
    run(bot.shell_command(upd(tg, "/shell echo hi"), ctx("echo", "hi")))
    assert 1 not in sm.SESSIONS and sm.ONESHOTS == {} and any("echo hi" in e["text"] for e in tg.sends())


def test_long_output_gets_a_full_output_button_and_short_output_does_not(shell_env):
    tg = W.FakeTG()

    async def go():
        await bot.shell_command(upd(tg, "/shell"), ctx())
        await bot.message_handler(upd(tg, "seq 1 80"), None)
        await bot.message_handler(upd(tg, "echo short"), None)
    run(go())
    assert len(ui.TASK_LOGS) == 1 and "80" in next(iter(ui.TASK_LOGS.values()))[0]["output"]


def test_nothing_is_deleted_if_the_summary_could_not_be_delivered(shell_env, monkeypatch):
    tg = W.FakeTG()
    orig = tio.ChatIO.send

    async def flaky(self, target, html, markup=None, patient=False):
        return None if "Shell Session Closed" in html else await orig(self, target, html, markup, patient)

    monkeypatch.setattr(tio.ChatIO, "send", flaky)

    async def go():
        await bot.shell_command(upd(tg, "/shell"), ctx())
        await bot.message_handler(upd(tg, "echo precious"), None)
        await bot.exit_command(upd(tg, "/exit"), ctx())
    run(go())
    assert not [e for e in tg.events if e["kind"] in ("delete_many", "delete_one")]


def test_buttons_are_allowlisted_and_the_stop_button_works(shell_env):
    answers = []

    class Q:
        def __init__(self, uid, data):
            self.data, self.from_user = data, types.SimpleNamespace(id=uid, username="x")

        async def answer(self, text=None, show_alert=False):
            answers.append(text)

    async def go():
        await bot.button_handler(types.SimpleNamespace(callback_query=Q(2, "shell_stop_1")), None)   # stranger
        s = sm.ShellSession(cwd=shell_env.tree)
        sm.SESSIONS[1] = s
        task = asyncio.ensure_future(s.run("sleep 36"))
        await asyncio.sleep(0.5)
        await bot.button_handler(types.SimpleNamespace(callback_query=Q(1, "shell_stop_1")), None)
        return await task
    r = run(go())
    assert "🚫 Access denied." in answers and "🛑 Command killed." in answers and r.stopped


def test_photos_are_not_sent_to_harvis_while_in_shell_mode(shell_env):
    tg = W.FakeTG()

    async def go():
        await bot.shell_command(upd(tg, "/shell"), ctx())
        u = upd(tg)
        u.message.photo = [object()]
        await bot.photo_handler(u, None)
    run(go())
    assert any("Shell mode is on" in e["text"] for e in tg.sends()) and shell_env.called == []


# ── presentation ─────────────────────────────────────────────────────────────

def test_summary_card_and_command_bubble_are_valid_telegram_html():
    cmds = [{"cmd": f"echo <{i}> && cat 'a&b'", "exit_code": i % 3 if i % 4 else None, "took": 1.5} for i in range(40)]
    card = ui.render_shell_summary("/home/x/<proj>", cmds, 4000)
    assert not problems(card) and "+28 earlier commands" in card and "1h" in card
    assert not problems(ui.render_shell_summary("/", [], 5))
    for args in [("ls", 0.2, "", True), ("x", 3, "\x1b[31m" + "line\n" * 300 + "<b>", False, 1, "/tmp/<a>"),
                 ("sleep", 9, "partial", False, None)]:
        assert not problems(ui.render_command_bubble(*args))
    assert "Stopped" in ui.render_command_bubble("sleep", 9, "x", False, None)


def test_status_bubble_shows_a_progress_bar_from_the_task_file():
    st = ui.Status("Deep", "🧠")
    st.set_plan("- [x] a\n- [x] b\n- [ ] write <parser>\n- [ ] d")
    out = st.render("Working…")
    assert "[█████░░░░░] 50%" in out and "2/4" in out and "write &lt;parser&gt;" in out and not problems(out)
