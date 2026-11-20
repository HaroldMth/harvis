"""shell_mode.py - the engine behind Telegram's /shell mode.

Every message becomes a bash command, and consecutive commands feel like ONE terminal:

* Each command runs in a fresh bash, wrapped so that its final working directory and exported
  environment are captured afterwards. `cd x`, `cd .. && make`, `export FOO=1` and
  `source venv/bin/activate` therefore all persist into the next command.
  (Shell functions, aliases and background-job control do not persist.)
* Commands run strictly one at a time per user, in the order they were sent.
* A hanging command can always be stopped (/stop or the Stop button): the whole process group is
  killed. Every command also has a timeout.
* Output is read in chunks (no 64 KiB line limit, `\\r` progress bars work) and capped in memory.
* On exit, one summary card replaces the clutter. Nothing is lost: the full output of the whole
  session is attached behind a "Step log" button, and HARVIS is handed a compact summary plus the
  final working directory.

The engine (ShellSession.run) has no Telegram dependency and is unit-tested directly.
"""

import asyncio
import codecs
import dataclasses
import logging
import os
import re
import shlex
import signal
import tempfile
import time
import uuid

import telegram_bot.tg_ui as ui

logger = logging.getLogger("harvis.shell")

DEFAULT_TIMEOUT = 900
MAX_OUTPUT_BYTES = 64 * 1024 * 1024   # a command that writes more than this is killed
MAX_LIVE_CHARS = 400_000        # in-memory buffer per command (the state trailer must fit inside)
MAX_KEPT_OUTPUT = 60_000        # per-command output kept for the summary / full log
LIVE_EDIT_EVERY = 1.5           # seconds between live bubble edits
_IGNORED_ENV = {"_", "SHLVL", "PWD", "OLDPWD", "__harvis_rc"}


# ── result / session ─────────────────────────────────────────────────────────

@dataclasses.dataclass
class CmdResult:
    cmd: str
    exit_code: int = None            # None = stopped by the user
    took: float = 0.0
    output: str = ""
    cwd_after: str = ""
    timed_out: bool = False
    stopped: bool = False
    blocked: bool = False
    truncated: bool = False

    def as_summary_dict(self) -> dict:
        return {"cmd": self.cmd, "exit_code": self.exit_code, "took": round(self.took, 1),
                "snippet": self.output[:120].replace("\n", " ")}


def blocked_reason(cmd: str):
    """Hard stop for catastrophic typos (rm -rf ~, mkfs on a disk...). Same list the agent's shell
    uses. Set HARVIS_SHELL_UNRESTRICTED=1 to disable when you really are the one at the keyboard."""
    if os.environ.get("HARVIS_SHELL_UNRESTRICTED") == "1":
        return None
    try:
        from tools.system import BLOCKLIST, BLOCK_PATTERNS
    except Exception:  # pragma: no cover
        return None
    if any(b in cmd for b in BLOCKLIST) or any(p.search(cmd) for p in BLOCK_PATTERNS):
        return "blocked: this looks destructive (matches a hard safety pattern). " \
               "Set HARVIS_SHELL_UNRESTRICTED=1 to allow it."
    return None


def default_cwd(user_id=None) -> str:
    """Project directory of the active session if there is one, else the user's home."""
    try:
        from db.store import get_active_session, get_session_info
        sid = get_active_session(user_id) if user_id is not None else None
        if sid and sid != "general":
            info = get_session_info(sid)
            if info and info.get("active_dir") and os.path.isdir(info["active_dir"]):
                return info["active_dir"]
    except Exception:
        pass
    return os.path.expanduser("~")


@dataclasses.dataclass
class ShellSession:
    cwd: str = dataclasses.field(default_factory=lambda: os.path.expanduser("~"))
    started_at: float = dataclasses.field(default_factory=time.monotonic)
    history: list = dataclasses.field(default_factory=list)        # [CmdResult]
    bubble_ids: list = dataclasses.field(default_factory=list)     # our messages, deleted on exit
    user_msg_ids: list = dataclasses.field(default_factory=list)   # user's command messages
    env_set: dict = dataclasses.field(default_factory=dict)        # exported changes that persist
    env_unset: set = dataclasses.field(default_factory=set)
    lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)
    _proc: object = None
    _stop_requested: bool = False

    # ── environment ──
    def _base_env(self) -> dict:
        try:
            from tools.session import graphical_env
            env = dict(graphical_env())
        except Exception:
            env = dict(os.environ)
        # friendly defaults the user can still override (their exports are applied afterwards)
        for k, v in {"TERM": "dumb", "NO_COLOR": "1", "PAGER": "cat", "GIT_PAGER": "cat",
                     "PYTHONUNBUFFERED": "1", "DEBIAN_FRONTEND": "noninteractive"}.items():
            env.setdefault(k, v)
        for k in self.env_unset:
            env.pop(k, None)
        env.update(self.env_set)
        return env

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    # ── control ──
    def stop(self) -> bool:
        """Kill the running command (whole process group). True if something was running."""
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return False
        self._stop_requested = True
        _kill_group(proc, signal.SIGTERM)
        try:
            asyncio.get_running_loop().call_later(2.0, _kill_group, proc, signal.SIGKILL)
        except RuntimeError:
            _kill_group(proc, signal.SIGKILL)
        return True

    # ── the engine ──
    async def run(self, cmd: str, on_output=None, timeout: float = None) -> CmdResult:
        """Run one command. on_output(text) is called (synchronously, cheap) as output arrives."""
        t0 = time.monotonic()
        res = CmdResult(cmd=cmd, cwd_after=self.cwd)
        reason = blocked_reason(cmd)
        if reason:
            res.blocked, res.exit_code, res.output = True, 126, reason
            return res

        token = uuid.uuid4().hex[:12]
        begin, end = f"\n@@HVS-{token}-STATE@@\n", f"@@HVS-{token}-END@@"
        script = (
            f"cd {shlex.quote(self.cwd)} 2>/dev/null || {{ echo 'cd: cannot enter {self.cwd}' >&2; exit 97; }}\n"
            f"{cmd}\n"
            f"__harvis_rc=$?\n"
            f"printf '%s' {shlex.quote(begin)}; pwd; env -0; printf '%s' {shlex.quote(end)}\n"
            f"exit $__harvis_rc\n"
        )
        env = self._base_env()
        self._stop_requested = False
        # Output goes to a temp file that we poll, not a pipe. With a pipe, a background job
        # (`server &`) keeps the pipe open and the command can never be seen as finished.
        out_path = os.path.join(tempfile.gettempdir(), f"hvs_{token}.out")
        try:
            out_f = open(out_path, "wb")
            proc = await asyncio.create_subprocess_exec(
                "/bin/bash", "-c", script,
                stdin=asyncio.subprocess.DEVNULL, stdout=out_f, stderr=asyncio.subprocess.STDOUT,
                env=env, start_new_session=True,
                cwd=self.cwd if os.path.isdir(self.cwd) else os.path.expanduser("~"),
            )
        except Exception as e:
            res.exit_code, res.output, res.took = 127, f"could not start bash: {e}", time.monotonic() - t0
            return res
        finally:
            try:
                out_f.close()          # the child has its own copy of the descriptor
            except Exception:
                pass
        self._proc = proc

        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        buf = ""
        truncated = False
        over_limit = False
        wait_task = asyncio.ensure_future(proc.wait())

        async def pump():
            nonlocal buf, truncated, over_limit
            with open(out_path, "rb") as rf:
                while True:
                    chunk = rf.read(65536)
                    if chunk:
                        buf += decoder.decode(chunk)
                        if len(buf) > MAX_LIVE_CHARS:
                            buf, truncated = buf[-MAX_LIVE_CHARS:], True
                        if on_output:
                            try:
                                on_output(_visible(buf, begin))
                            except Exception:  # a display problem must never break the command
                                logger.debug("on_output failed", exc_info=True)
                        continue
                    if wait_task.done():
                        return                                  # process exited and the file is drained
                    try:
                        if os.path.getsize(out_path) > MAX_OUTPUT_BYTES:
                            over_limit = True
                            _kill_group(proc, signal.SIGKILL)   # `yes`-style flood: protect the disk
                    except OSError:
                        pass
                    await asyncio.sleep(0.1)

        try:
            await asyncio.wait_for(pump(), timeout or DEFAULT_TIMEOUT)
        except asyncio.TimeoutError:
            res.timed_out = True
            _kill_group(proc, signal.SIGKILL)
        except Exception as e:
            logger.warning(f"shell read failed: {e}")
            _kill_group(proc, signal.SIGKILL)
        try:
            await asyncio.wait_for(asyncio.shield(wait_task), 5)
        except asyncio.TimeoutError:
            _kill_group(proc, signal.SIGKILL)
            await wait_task
        self._proc = None
        try:
            os.unlink(out_path)
        except OSError:
            pass
        if over_limit:
            truncated = True

        # ── split output from the state trailer, apply cwd/env changes ──
        i = buf.find(begin)
        text = buf[:i] if i != -1 else buf
        if i != -1 and end in buf[i:]:
            state = buf[i + len(begin):buf.index(end, i)]
            cwd_line, _, env_blob = state.partition("\n")
            if cwd_line and os.path.isdir(cwd_line):
                self.cwd = cwd_line
            self._absorb_env(env, env_blob)
        res.cwd_after = self.cwd
        res.truncated = truncated or len(text) > MAX_KEPT_OUTPUT
        res.output = ui.clean_ansi(text[-MAX_KEPT_OUTPUT:]).strip("\n")
        res.took = time.monotonic() - t0
        if self._stop_requested:
            res.stopped, res.exit_code = True, None
        elif over_limit:
            res.exit_code = 137
            res.output += f"\n[killed: output exceeded {MAX_OUTPUT_BYTES // (1024 * 1024)} MB]"
        elif res.timed_out:
            res.exit_code = 124
            res.output += f"\n[timed out after {int(timeout or DEFAULT_TIMEOUT)}s and was killed]"
        else:
            res.exit_code = proc.returncode
        self.history.append(res)
        return res

    def _absorb_env(self, before: dict, blob: str):
        after = {}
        for item in blob.split("\x00"):
            k, sep, v = item.partition("=")
            if sep and k and "\n" not in k:
                after[k] = v
        if not after:
            return
        for k, v in after.items():
            if k not in _IGNORED_ENV and before.get(k) != v:
                self.env_set[k] = v
                self.env_unset.discard(k)
        for k in before:
            if k not in after and k not in _IGNORED_ENV:
                self.env_unset.add(k)
                self.env_set.pop(k, None)


def _visible(buf: str, begin: str) -> str:
    """The part of the buffer that is real output (hide the state trailer and any partial marker)."""
    i = buf.find(begin)
    if i != -1:
        return buf[:i]
    for k in range(min(len(begin) - 1, len(buf)), 0, -1):    # trailing text that might be a marker start
        if begin.startswith(buf[-k:]):
            return buf[:-k]
    return buf


def _kill_group(proc, sig):
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


# ── Telegram flow ────────────────────────────────────────────────────────────

SESSIONS: dict = {}            # user_id -> ShellSession (shell MODE is on)
ONESHOTS: dict = {}            # user_id -> throwaway session of a `/shell <cmd>` (so /stop can reach it)


def stop_running(user_id) -> bool:
    """Kill whatever shell command this user has running (mode or one-shot)."""
    hit = False
    for registry in (SESSIONS, ONESHOTS):
        sess = registry.get(user_id)
        hit = bool(sess and sess.stop()) or hit
    return hit


def stop_keyboard(user_id):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Stop", callback_data=f"shell_stop_{user_id}")]])


def _entry(i: int, r: CmdResult) -> dict:
    """One row of the full-session log (same shape the 'Step log' button already renders)."""
    return {"step": i, "tool": "shell", "args": {"cmd": r.cmd, "cwd": r.cwd_after},
            "ok": r.exit_code == 0, "secs": round(r.took, 1), "output": r.output}


async def run_in_session(update, sess: ShellSession, cmd: str, io, user_id):
    """Run one command inside the session: ordered, live bubble, sealed result."""
    sess.user_msg_ids.append(update.message.message_id)          # tracked even while queued
    async with sess.lock:                                         # commands never overlap or reorder
        t0 = time.monotonic()
        kb = stop_keyboard(user_id)
        bubble = await io.send(update.message, ui.render_command_bubble(cmd, 0.0, "", True, cwd=sess.cwd), kb)
        if bubble is not None:
            sess.bubble_ids.append(bubble.message_id)
        last = [0.0]

        def on_output(text):
            now = time.monotonic()
            if bubble is not None and now - last[0] >= LIVE_EDIT_EVERY:
                last[0] = now
                io.edit(bubble, ui.render_command_bubble(cmd, now - t0, text, True, cwd=sess.cwd), kb)

        res = await sess.run(cmd, on_output)
        long_output = res.output.count("\n") >= 16 or res.truncated
        markup = None
        if long_output:                                           # the bubble only shows the last lines
            task_id = uuid.uuid4().hex[:10]
            ui.store_log(task_id, [_entry(1, res)])
            markup = ui.details_keyboard(task_id)
        final = ui.render_command_bubble(cmd, res.took, res.output, False, res.exit_code, res.cwd_after)
        if bubble is not None:
            await io.edit_now(bubble, final, markup)
        else:                                                     # the live bubble could not be sent
            sent = await io.send(update.message, final, markup, patient=True)
            if sent is not None:
                sess.bubble_ids.append(sent.message_id)
        return res


def handoff_text(cwd: str, results: list, duration: float) -> str:
    """Compact note for HARVIS's conversation memory (kept short: the chat prefix is capped)."""
    if not results:
        return f"[The user opened and closed a shell session ({ui.fmt_elapsed(duration)}); no commands were run.]"
    parts = []
    for r in results[-12:]:
        if r.exit_code == 0:
            parts.append(f"`{r.cmd[:70]}` ok")
        elif r.stopped:
            parts.append(f"`{r.cmd[:70]}` stopped")
        else:
            err = next((l for l in reversed(r.output.splitlines()) if l.strip()), "")[:80]
            parts.append(f"`{r.cmd[:70]}` failed (exit {r.exit_code}){': ' + err if err else ''}")
    more = f" (+{len(results) - 12} earlier)" if len(results) > 12 else ""
    return (f"[The user ran {len(results)} command(s) himself in shell mode over {ui.fmt_elapsed(duration)}"
            f"{more}: " + "; ".join(parts) + f". The working directory is now {cwd}.]")


async def finish_session(update, user_id, io, extra_user_msg_ids=()):
    """Leave shell mode: one summary card, clean up the clutter, hand context to HARVIS."""
    sess = SESSIONS.pop(user_id, None)
    if sess is None:
        return False
    sess.stop()
    try:                                       # let a just-killed command finish recording itself
        await asyncio.wait_for(sess.lock.acquire(), 6)
        sess.lock.release()
    except asyncio.TimeoutError:
        pass
    duration = time.monotonic() - sess.started_at
    results = list(sess.history)

    task_id = uuid.uuid4().hex[:10]
    markup = None
    if results:
        ui.store_log(task_id, [_entry(i, r) for i, r in enumerate(results, 1)])
        markup = ui.details_keyboard(task_id)
    summary = ui.render_shell_summary(sess.cwd, [r.as_summary_dict() for r in results], duration)
    sent = await io.send(update.message, summary, markup, patient=True)

    # Only clean up once the summary is safely delivered; otherwise the output would be gone.
    if sent is not None:
        ids = list(dict.fromkeys(sess.user_msg_ids + sess.bubble_ids + list(extra_user_msg_ids)))
        try:
            await io.delete_many(update.effective_chat.bot, update.effective_chat.id, ids)
        except Exception as e:
            logger.warning(f"shell cleanup failed: {e}")

    # Hand over to HARVIS: memory note + the working directory as the project directory.
    try:
        from db.store import get_active_session, save_message, update_session
        sid = get_active_session(user_id)
        save_message(f"telegram_{user_id}_{sid}", "assistant", handoff_text(sess.cwd, results, duration))
        if sid and sid != "general" and os.path.isdir(sess.cwd):
            update_session(sid, active_dir=sess.cwd)
    except Exception as e:
        logger.warning(f"shell handoff failed: {e}")
    return True
