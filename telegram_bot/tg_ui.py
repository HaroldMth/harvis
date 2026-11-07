"""tg_ui.py - Telegram HTML rendering for HARVIS.

Drop next to bot.py (telegram_bot/tg_ui.py) and `import tg_ui as ui`.
Everything here returns Telegram-HTML strings (parse_mode=HTML), never Markdown.
Supported tags used: b, i, s, code, pre, a, blockquote (expandable).
"""

import html
import json
import re
import time
from collections import OrderedDict

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

MAX = 4096
KEEP_STEP_BUBBLES = False   # True = leave the per-tool bubbles in chat after the run

# ─── Basics ──────────────────────────────────────────────────────────────────

def esc(s) -> str:
    return html.escape(str(s), quote=False)


def trunc(s, n: int) -> str:
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def head_tail(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    h = int(n * 0.6)
    t = n - h
    return f"{s[:h]}\n… [{len(s) - n:,} chars hidden] …\n{s[-t:]}"


def strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s))


# ─── Markdown -> Telegram HTML ───────────────────────────────────────────────

_FENCE = re.compile(r"```([\w+-]*)[ \t]*\n?(.*?)(?:```|\Z)", re.S)
_INLINE = re.compile(r"`([^`\n]+)`")
_TABLE = re.compile(r"(?:^[ \t]*\|.*\|[ \t]*(?:\n|\Z))+", re.M)
_HEAD = re.compile(r"^#{1,6}\s+(.+)$", re.M)
_BULLET = re.compile(r"^(\s*)[-*]\s+", re.M)
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_ITALIC = re.compile(r"(?<![\*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\*\w])")
_STRIKE = re.compile(r"~~(.+?)~~")
_L_BOLD = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])")      # old Telegram "Markdown" style
_L_ITALIC = re.compile(r"(?<![\w])_(?!\s)(.+?)(?<!\s)_(?![\w])")


def md_to_html(text: str, legacy: bool = False) -> str:
    """Convert model-style Markdown to Telegram HTML.
    legacy=True treats *x* as bold and _x_ as italic (old Telegram Markdown strings)."""
    if not text:
        return ""
    stash = []

    def keep(h: str) -> str:
        stash.append(h)
        return f"\x00{len(stash) - 1}\x00"

    def fence(m):
        lang, body = m.group(1), m.group(2).rstrip("\n")
        b = esc(body)
        if lang:
            return keep(f'<pre><code class="language-{lang}">{b}</code></pre>')
        return keep(f"<pre>{b}</pre>")

    text = _FENCE.sub(fence, text)
    text = _TABLE.sub(lambda m: keep(f"<pre>{esc(m.group(0).strip(chr(10)))}</pre>"), text)
    text = _INLINE.sub(lambda m: keep(f"<code>{esc(m.group(1))}</code>"), text)
    text = esc(text)

    text = _HEAD.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = _BULLET.sub(lambda m: f"{m.group(1)}• ", text)
    text = _LINK.sub(lambda m: f'<a href="{m.group(2).replace(chr(34), "%22")}">{m.group(1)}</a>', text)
    if legacy:
        text = _L_BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", text)
        text = _L_ITALIC.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    else:
        text = _BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", text)
        text = _ITALIC.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    text = _STRIKE.sub(lambda m: f"<s>{m.group(1)}</s>", text)

    return re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)


# ─── Chunking (keeps code fences balanced, stays under 4096 after conversion) ─

def split_md(text: str, limit: int = 3000) -> list:
    lines = []
    for l in text.split("\n"):
        while len(l) > limit:
            lines.append(l[:limit])
            l = l[limit:]
        lines.append(l)

    chunks, cur = [], ""
    in_fence, fence_open = False, ""
    for line in lines:
        if cur and len(cur) + len(line) + 1 > limit:
            if in_fence:
                cur += "\n```"
            chunks.append(cur)
            cur = fence_open if in_fence else ""
        cur = f"{cur}\n{line}" if cur else line
        if line.strip().startswith("```"):
            if in_fence:
                in_fence, fence_open = False, ""
            else:
                in_fence, fence_open = True, line.strip()
    if cur.strip():
        chunks.append(cur)
    return chunks or [""]


def render_final(text: str, limit: int = 3000) -> list:
    """Model text -> list of Telegram-HTML chunks, each <= 4096 chars."""
    out = []
    for c in split_md(text, limit):
        h = md_to_html(c)
        if len(h) > MAX and limit > 400:
            out += render_final(c, limit // 2)
        else:
            out.append(h)
    return out


# ─── Thinking blocks ─────────────────────────────────────────────────────────

_THINK = re.compile(r"<think>(.*?)(?:</think>|\Z)", re.S)


def split_think(buf: str):
    """Returns (think_text, answer_text) from a streaming buffer."""
    if "<think>" not in buf:
        return "", buf
    think = "".join(_THINK.findall(buf)).strip()
    answer = _THINK.sub("", buf).strip()
    return think, answer


def think_view(text: str, done: bool = False) -> str:
    title = "🧠 <b>Thinking</b> ✅" if done else "🧠 <b>Thinking…</b>"
    body = esc(head_tail(text.strip(), 1800))
    return f"{title}\n<blockquote expandable>{body}</blockquote>"


# ─── Tools ───────────────────────────────────────────────────────────────────

TOOLS = {
    "shell_exec": ("💻", "Shell"), "system_info": ("🖥️", "System info"),
    "send_telegram": ("📨", "Telegram"), "check_emails": ("📬", "Check emails"),
    "send_email": ("📧", "Send email"), "screenshot": ("📸", "Screenshot"),
    "open_app": ("🚀", "Open app"), "close_app": ("🛑", "Close app"),
    "play_song": ("🎵", "Play song"), "notify": ("🔔", "Notify"),
    "volume_control": ("🔊", "Volume"), "brightness_control": ("💡", "Brightness"),
    "timer": ("⏱️", "Timer"), "media_control": ("⏯️", "Media"),
    "weather": ("🌤️", "Weather"), "clipboard": ("📋", "Clipboard"),
    "spawn_subagent": ("🤖", "Subagent"), "web_search": ("🔍", "Web search"),
    "browser_fetch": ("🌐", "Browser"), "read_file": ("📖", "Read file"),
    "edit_file": ("✏️", "Edit file"), "file_op": ("📁", "File op"),
}


def tool_meta(name: str):
    return TOOLS.get(name, ("🛠️", name))


def _kv(args: dict) -> str:
    lines = []
    for k, v in list(args.items())[:6]:
        if not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False, default=str)
        v = v.replace("\n", " ↵ ")
        lines.append(f"<b>{esc(k)}</b>: <code>{esc(trunc(v, 200))}</code>")
    if len(args) > 6:
        lines.append(f"<i>+{len(args) - 6} more args</i>")
    return "\n".join(lines)


def fmt_args(name: str, args: dict) -> str:
    if not args:
        return "<i>no args</i>"
    if name == "shell_exec":
        for key in ("command", "cmd", "script"):
            if key in args:
                rest = {k: v for k, v in args.items() if k != key}
                s = f'<pre><code class="language-bash">{esc(trunc(args[key], 600))}</code></pre>'
                return s + (("\n" + _kv(rest)) if rest else "")
    return _kv(args)


def step_running(step, name, args) -> str:
    emoji, label = tool_meta(name)
    return f"{emoji} <b>Step {step} · {esc(label)}</b>  ⏳\n{fmt_args(name, args)}\n<i>running…</i>"


def step_done(step, name, args, out: str, ok: bool, secs: float) -> str:
    emoji, label = tool_meta(name)
    icon = "✅" if ok else "❌"
    head = f"{emoji} <b>Step {step} · {esc(label)}</b>  {icon}  <i>{secs:.1f}s</i>\n{fmt_args(name, args)}"
    out = (out or "").strip()
    if not out:
        return head + "\n<i>no output</i>"
    s = head
    for lim in (1400, 700, 300):
        title = "Output" if ok else "Error"
        body = esc(head_tail(out, lim))
        s = (f"{head}\n<b>{title}</b> <i>({len(out):,} chars)</i>\n"
             f"<blockquote expandable><code>{body}</code></blockquote>")
        if len(s) <= 4000:
            return s
    return s[:MAX]


# ─── Live status bubble ──────────────────────────────────────────────────────

class Status:
    def __init__(self, label: str, emoji: str):
        self.label, self.emoji = label, emoji
        self.t0 = time.time()
        self.steps = OrderedDict()   # step -> [tool name, icon]
        self.log = []

    def elapsed(self) -> float:
        return time.time() - self.t0

    def start(self, step, name):
        self.steps[step] = [name, "⏳"]

    def finish(self, step, ok, name, args, out, secs):
        if step in self.steps:
            self.steps[step][1] = "✅" if ok else "❌"
        self.log.append({"step": step, "tool": name, "args": args, "ok": ok,
                         "secs": round(secs, 2), "output": out or ""})

    def header(self) -> str:
        n = len(self.steps)
        tools = f" · 🔧 {n}" if n else ""
        return f"{self.emoji} <b>{esc(self.label)}</b> · ⏱ {self.elapsed():.0f}s{tools}"

    def render(self, note: str = "Working…") -> str:
        items = list(self.steps.items())[-6:]
        hidden = len(self.steps) - len(items)
        tl = "\n".join(f"{ic} <code>{esc(nm)}</code>" for _, (nm, ic) in items)
        more = f"<i>+{hidden} earlier steps</i>\n" if hidden > 0 else ""
        body = f"{more}{tl}\n\n" if tl else ""
        return f"{self.header()}\n\n{body}<i>{esc(note)}</i>"


def stream_view(answer_text: str, status: Status) -> str:
    """Streaming reply: compact header + the tail of the answer so far."""
    t = answer_text if len(answer_text) <= 2800 else "…" + answer_text[-2800:]
    h = f"{status.header()}\n\n{md_to_html(t)}"
    if len(h) > 4000:
        h = f"{status.header()}\n\n{esc(t[-1500:])}"
    return h


def footer(status: Status) -> str:
    n = len(status.steps)
    tools = f" · 🔧 {n} step{'s' if n != 1 else ''}" if n else ""
    return f"\n\n<i>{status.emoji} {esc(status.label)} · ⏱ {status.elapsed():.1f}s{tools}</i>"


# ─── Step log (for the "Details" button) ─────────────────────────────────────

TASK_LOGS = OrderedDict()


def store_log(task_id: str, log: list):
    TASK_LOGS[task_id] = log
    while len(TASK_LOGS) > 30:
        TASK_LOGS.popitem(last=False)


def details_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("📋 Step log", callback_data=f"log_{task_id}")]])


def render_log(log: list) -> str:
    parts = []
    for e in log:
        parts.append(
            f"=== Step {e['step']} · {e['tool']} · {'OK' if e['ok'] else 'FAILED'} · {e['secs']}s ===\n"
            f"ARGS: {json.dumps(e['args'], ensure_ascii=False, default=str, indent=2)}\n"
            f"OUTPUT:\n{e['output']}\n"
        )
    return "\n".join(parts) or "No tool steps in this run."
