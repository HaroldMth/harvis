"""tg_io.py - rate-limit-safe, non-blocking Telegram output for HARVIS.

Why this exists
---------------
The old bot awaited several Telegram calls inside the agent's progress callback (status edit,
step bubble send, bubble edit, status edit ... per tool step), fired extra edits from the
streaming thread, and on any send failure retried by sending a SECOND request. Telegram allows
roughly one message per second per chat; past that it answers 429 RetryAfter, and hammering
through the ban makes the ban longer. Replies then got slower and slower, and a final answer
that hit the limit was simply lost.

What this does
--------------
* One ChatIO per chat. Every API call goes through one lock with a minimum gap between calls.
* edit() is non-blocking and coalescing: only the NEWEST text for a message is ever sent.
  The agent never waits on Telegram.
* RetryAfter is waited out (plus a margin) and the same call is retried - never duplicated.
* Network errors back off and retry. Parse errors fall back to plain text exactly once.
* send() never raises; the final answer uses patient=True for extra persistence.
* Everything is sanitized (telegram_bot.tg_ui.fit_html) before it leaves, so a malformed tag
  can no longer make Telegram reject the whole message.
"""

import asyncio
import logging
import re
import time

import telegram_bot.tg_ui as ui

try:
    from telegram.constants import ParseMode
    from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut
    HTML = ParseMode.HTML
except Exception:  # pragma: no cover - lets the module import (and be unit-tested) without PTB
    HTML = "HTML"

    class _TgErr(Exception):
        pass

    class RetryAfter(_TgErr):
        def __init__(self, retry_after=1):
            super().__init__(f"Flood control exceeded. Retry in {retry_after} seconds")
            self.retry_after = retry_after

    class NetworkError(_TgErr):
        pass

    class TimedOut(NetworkError):
        pass

    class BadRequest(_TgErr):
        pass

    class Forbidden(_TgErr):
        pass

logger = logging.getLogger("harvis.tg_io")

_PARSE_ERR = re.compile(r"can't parse entities|can't find end|unsupported start tag|unexpected end tag|"
                        r"entities|unclosed|tag", re.I)
_NOT_MODIFIED = re.compile(r"not modified", re.I)
_GONE = re.compile(r"message to edit not found|message can't be edited|message_id_invalid|"
                   r"message to delete not found|message to be edited|chat not found", re.I)


def _retry_seconds(err) -> float:
    ra = getattr(err, "retry_after", 1)
    if hasattr(ra, "total_seconds"):
        ra = ra.total_seconds()
    try:
        return max(0.5, float(ra))
    except Exception:
        return 1.0


class ChatIO:
    def __init__(self, gap: float = 1.05, retries: int = 6, patient_retries: int = 14,
                 margin: float = 0.5, now=time.monotonic, sleep=asyncio.sleep):
        self.gap = gap
        self.margin = margin
        self.retries = retries
        self.patient_retries = patient_retries
        self._now, self._sleep = now, sleep
        self._lock = asyncio.Lock()
        self._next = 0.0
        self._pending = {}     # key -> (msg, html, markup); newest wins
        self._tasks = {}       # key -> flushing task
        self._last = {}        # key -> html last applied (skip identical edits)
        self._gone = set()
        self.calls = 0         # API calls actually issued (for tests / diagnostics)

    # ── core: one rate-limited, retrying call ─────────────────────────────────
    async def _call(self, make, patient: bool = False):
        async with self._lock:
            attempts = (self.patient_retries if patient else self.retries) + 1
            last = None
            for attempt in range(attempts):
                wait = self._next - self._now()
                if wait > 0:
                    await self._sleep(wait)
                try:
                    self.calls += 1
                    res = await make()
                    self._next = self._now() + self.gap
                    return res
                except BadRequest:
                    raise                                     # permanent (bad HTML, not modified...). In PTB it
                                                              # subclasses NetworkError, so it must be caught first
                except Forbidden:
                    raise                                     # blocked / kicked: retrying cannot help
                except RetryAfter as e:                       # honour the ban; do NOT send anything else
                    delay = _retry_seconds(e) + self.margin
                    logger.warning(f"Telegram flood control: waiting {delay:.1f}s")
                    self._next = self._now() + delay
                    last = e
                except (TimedOut, NetworkError) as e:         # transient network trouble
                    self._next = self._now() + min(2 ** attempt, 20)
                    last = e
            raise last if last else RuntimeError("telegram call failed")

    # ── sending ───────────────────────────────────────────────────────────────
    async def send(self, target, html: str, markup=None, patient: bool = False):
        """Reply to `target` (a Message). Returns the sent Message, or None if it could not be sent."""
        h = ui.fit_html(html)

        async def go(text, mode):
            return await target.reply_text(text, parse_mode=mode, reply_markup=markup,
                                           disable_web_page_preview=True)

        try:
            return await self._call(lambda: go(h, HTML), patient)
        except Exception as e:
            if isinstance(e, BadRequest) and _PARSE_ERR.search(str(e)):
                try:
                    return await self._call(lambda: go(ui.strip_tags(h)[:4096] or "…", None), patient)
                except Exception as e2:
                    logger.error(f"send failed after plain fallback: {e2}")
                    return None
            logger.error(f"send failed: {type(e).__name__}: {e}")
            return None

    async def send_document(self, target, data: bytes, filename: str, caption: str = ""):
        """Last-resort delivery when a long answer cannot be sent as messages."""
        import io as _io

        async def go():
            buf = _io.BytesIO(data)
            buf.name = filename
            return await target.reply_document(buf, caption=caption[:900] or None)

        try:
            return await self._call(go, patient=True)
        except Exception as e:
            logger.error(f"document fallback failed: {e}")
            return None

    # ── editing (non-blocking, coalescing) ────────────────────────────────────
    @staticmethod
    def _key(msg):
        return getattr(msg, "message_id", None) or id(msg)

    def edit(self, msg, html: str, markup=None):
        """Schedule an edit and return immediately. Newer text for the same message replaces
        older text that has not been sent yet. Must be called on the event-loop thread."""
        if msg is None or not html:
            return
        key = self._key(msg)
        if key in self._gone:
            return
        self._pending[key] = (msg, html, markup)
        task = self._tasks.get(key)
        if task is None or task.done():
            self._tasks[key] = asyncio.get_running_loop().create_task(self._flush(key))

    async def _apply_edit(self, key):
        item = self._pending.pop(key, None)
        if item is None:
            return None
        msg, html, markup = item
        h = ui.fit_html(html)
        if self._last.get(key) == (h, markup is not None):
            return None
        try:
            res = await msg.edit_text(h, parse_mode=HTML, reply_markup=markup, disable_web_page_preview=True)
            self._last[key] = (h, markup is not None)
            return res
        except BadRequest as e:
            s = str(e)
            if _NOT_MODIFIED.search(s):
                self._last[key] = (h, markup is not None)
                return None
            if _GONE.search(s):
                self._gone.add(key)
                return None
            if _PARSE_ERR.search(s):
                return await msg.edit_text(ui.strip_tags(h)[:4096] or "…", reply_markup=markup)
            raise

    async def _flush(self, key):
        while key in self._pending and key not in self._gone:
            try:
                # the newest text is picked up INSIDE the rate-limited call, after waiting its turn
                await self._call(lambda: self._apply_edit(key))
            except Exception as e:
                logger.warning(f"edit failed: {type(e).__name__}: {e}")
                self._pending.pop(key, None)
                return

    async def edit_now(self, msg, html: str, markup=None):
        """Edit and wait for it to be applied (button handlers)."""
        self.edit(msg, html, markup)
        await self.drain()

    # ── housekeeping ──────────────────────────────────────────────────────────
    def discard(self, msg):
        """Drop edits that have not been sent yet (call before deleting a message)."""
        if msg is not None:
            self._pending.pop(self._key(msg), None)

    async def delete(self, msg):
        if msg is None:
            return
        self.discard(msg)
        self._gone.add(self._key(msg))
        try:
            await self._call(msg.delete)
        except Exception:
            pass

    async def drain(self, timeout: float = 30.0):
        """Wait for scheduled edits to finish."""
        tasks = [t for t in self._tasks.values() if not t.done()]
        if tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout)
            except asyncio.TimeoutError:
                pass


_IOS = {}


def get_io(chat_id) -> ChatIO:
    """One ChatIO per chat (the rate limit is per chat). Keyed by event loop too, because an
    asyncio.Lock must not be shared across loops."""
    key = (chat_id, id(asyncio.get_running_loop()))
    io = _IOS.get(key)
    if io is None:
        io = _IOS[key] = ChatIO()
    return io
