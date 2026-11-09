"""tg_ui.py - Telegram HTML rendering for HARVIS.

Drop next to bot.py (telegram_bot/tg_ui.py) and `import tg_ui as ui`.
Everything here returns Telegram-HTML strings (parse_mode=HTML), never Markdown.
Supported tags used: b, i, s, code, pre, a, blockquote (expandable).
"""

import html
import json
import re
import time
import unicodedata
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


# ─── Telegram-HTML sanitizer ─────────────────────────────────────────────────
# Telegram rejects the WHOLE message ("can't parse entities") on a single bad tag, and counts
# the 4096-character limit on the text AFTER parsing. Everything we send goes through
# sanitize_html(), which guarantees balanced, properly nested, whitelisted tags and
# escaped text, and can truncate on a visible-character budget without cutting a tag in half.

_TAG_RE = re.compile(
    r"<(/?)(b|strong|i|em|u|ins|s|strike|del|a|code|pre|blockquote|tg-spoiler)((?:\s+[^<>]*?)?)\s*>",
    re.I)
_CANON = {"strong": "b", "em": "i", "ins": "u", "strike": "s", "del": "s"}
_INLINE_TAGS = {"b", "i", "u", "s", "a", "tg-spoiler"}
_BLOCK_TAGS = {"pre", "blockquote"}
_HREF_RE = re.compile(r"""href\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
_LANG_RE = re.compile(r"""class\s*=\s*["'](language-[\w+#.-]+)["']""", re.I)


def _open_tag(name: str, attrs: str):
    """Rebuild an opening tag keeping only attributes Telegram understands. None = drop the tag."""
    if name == "a":
        m = _HREF_RE.search(attrs or "")
        href = (m.group(1) or m.group(2) or "").strip() if m else ""
        if not re.match(r"(https?://|tg://|mailto:)", href, re.I):
            return None
        return f'<a href="{href.replace(chr(34), "%22")}">'
    if name == "code":
        m = _LANG_RE.search(attrs or "")
        return f'<code class="{m.group(1)}">' if m else "<code>"
    if name == "blockquote":
        return "<blockquote expandable>" if re.search(r"\bexpandable\b", attrs or "", re.I) else "<blockquote>"
    return f"<{name}>"


def visible_len(h: str) -> int:
    return len(html.unescape(re.sub(r"<[^>]+>", "", h)))


def sanitize_html(h: str, max_visible: int = None, ellipsis: str = "…") -> str:
    """Return valid Telegram HTML for `h`.

    - unknown / malformed tags become literal text; text is re-escaped
    - mis-nested inline tags are repaired (<b><i>x</b></i> -> <b><i>x</i></b><i></i>)
    - nothing inside <code>/<pre> except the <pre><code> wrapper; no nested blockquotes
    - optional visible-character budget with clean truncation
    """
    if not h:
        return ""
    out = []
    stack = []            # entries: {"name", "open", "saved", "drop"}
    budget = max_visible
    truncated = False

    def emit_text(raw):
        nonlocal budget, truncated
        t = html.unescape(raw)
        if budget is not None:
            if len(t) > budget:
                t, truncated = t[:max(budget, 0)], True
            budget -= len(t)
        out.append(esc(t))

    def closers_for(entries):
        return "".join(f"</{e['name']}>" for e in reversed(entries) if not e["drop"])

    def open_inline_stack():
        return [e for e in stack if not e["drop"] and e["name"] in _INLINE_TAGS]

    pos = 0
    for m in _TAG_RE.finditer(h):
        if truncated:
            break
        if m.start() > pos:
            emit_text(h[pos:m.start()])
            if truncated:
                break
        pos = m.end()
        closing, name, attrs = m.group(1) == "/", m.group(2).lower(), m.group(3)
        name = _CANON.get(name, name)
        in_code = any(e["name"] == "code" and not e["drop"] for e in stack)
        in_pre = any(e["name"] == "pre" and not e["drop"] for e in stack)

        if not closing:
            tag = _open_tag(name, attrs)
            drop = tag is None
            if not drop and in_code:
                drop = True                                   # nothing nests inside <code>
            elif not drop and in_pre and name != "code":
                drop = True                                   # only <pre><code> allowed inside <pre>
            elif not drop and name == "code" and in_pre and any(
                    e["name"] == "code" and not e["drop"] for e in stack):
                drop = True
            elif not drop and name == "blockquote" and any(e["name"] == "blockquote" and not e["drop"] for e in stack):
                drop = True                                   # blockquotes cannot nest
            elif not drop and name == "a" and any(e["name"] == "a" and not e["drop"] for e in stack):
                drop = True
            entry = {"name": name, "open": tag, "saved": [], "drop": drop}
            if not drop and name in _BLOCK_TAGS:
                # block elements start clean: park the open inline tags, restore them afterwards
                saved = open_inline_stack()
                if saved:
                    out.append(closers_for(saved))
                    entry["saved"] = saved
                    for e in saved:
                        stack.remove(e)
            if not drop:
                out.append(tag)
            stack.append(entry)
        else:
            idx = next((i for i in range(len(stack) - 1, -1, -1) if stack[i]["name"] == name), None)
            if idx is None:
                continue                                       # stray closer: ignore
            above = stack[idx + 1:]
            target = stack[idx]
            # close everything opened after the target, close the target, reopen the inline ones
            out.append(closers_for(above))
            if not target["drop"]:
                out.append(f"</{name}>")
            del stack[idx:]
            if not target["drop"] and target["saved"]:
                for e in target["saved"]:
                    out.append(e["open"])
                    stack.append(e)
            for e in above:
                if e["drop"]:
                    stack.append(e)
                elif e["name"] in _INLINE_TAGS:
                    out.append(e["open"])
                    stack.append(e)
    if not truncated and pos < len(h):
        emit_text(h[pos:])
    if truncated and ellipsis:
        out.append(esc(ellipsis))
    out.append(closers_for(stack))
    res = "".join(out)
    # empty formatting pairs add nothing and can leave an "empty message"
    for _ in range(3):
        res = re.sub(r"<(b|i|u|s|tg-spoiler)></\1>", "", res)
    return res


def fit_html(h: str, limit: int = MAX) -> str:
    """Sanitized and guaranteed to fit Telegram's limit (counted on visible text)."""
    out = sanitize_html(h, max_visible=limit - 1)
    return out if out.strip() else "…"


# ─── Markdown -> Telegram HTML ───────────────────────────────────────────────

_FENCE = re.compile(r"(?:```|~~~)([\w+#.-]*)[ \t]*\n?(.*?)(?:```|~~~|\Z)", re.S)
_INLINE = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.S)
_TABLE = re.compile(r"(?:^[ \t]*\|.*\|[ \t]*(?:\n|\Z)){2,}", re.M)
_ESCAPED = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|~>])")
_HEAD = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)
_HR = re.compile(r"^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$", re.M)
_TASK = re.compile(r"^(\s*)[-*+]\s+\[([ xX])\]\s+", re.M)
_BULLET = re.compile(r"^([ \t]*)[-*+][ \t]+(?=\S)", re.M)
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://(?:[^()\s]|\([^()\s]*\))+)\)")
_BOLD = re.compile(r"(?<!\*)\*\*(?!\s)(.+?)(?<!\s)\*\*(?!\*)|(?<![\w_])__(?!\s)(.+?)(?<!\s)__(?![\w_])", re.S)
_ITALIC = re.compile(r"(?<![\*\w])\*(?!\s|\*)([^*\n]+?)(?<!\s)\*(?![\*\w])")
_ITALIC_U = re.compile(r"(?<![\w_])_(?!\s|_)([^_\n]+?)(?<!\s)_(?![\w_])")
_STRIKE = re.compile(r"~~(?!\s)(.+?)(?<!\s)~~")
_SPOILER = re.compile(r"\|\|(?!\s)(.+?)(?<!\s)\|\|")
_L_BOLD = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])")      # old Telegram "Markdown" style
_L_ITALIC = re.compile(r"(?<![\w])_(?!\s)(.+?)(?<!\s)_(?![\w])")
_RAW_TAG = re.compile(r"&lt;(/?)(b|strong|i|em|u|s)&gt;", re.I)
_BR = re.compile(r"&lt;br\s*/?&gt;", re.I)
_QUOTE_LINE = re.compile(r"^[ \t]*&gt;[ \t]?(.*)$")


def _width(s: str) -> int:
    """Display width: CJK/emoji count double so table columns line up in monospace."""
    w = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") or ord(ch) >= 0x1F300 else 1
    return w


def _pad(s: str, n: int) -> str:
    return s + " " * max(0, n - _width(s))


def _split_row(line: str):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)]


_SEP_CELL = re.compile(r"^:?-{1,}:?$")


def _render_table(block: str) -> str:
    """Markdown table -> something that reads well on a phone.
    Narrow: aligned monospace grid.  Wide 2-column: 'key — value' lines.  Wide N-column: one card per row."""
    rows = [_split_row(l) for l in block.strip("\n").split("\n") if l.strip()]
    rows = [r for r in rows if not all(_SEP_CELL.match(c) or c == "" for c in r)]
    if not rows:
        return esc(block)
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    # inline markup inside cells is not rendered in a grid; drop the markers
    clean = lambda c: re.sub(r"(\*\*|__|~~|`)", "", c)
    rows = [[clean(c) for c in r] for r in rows]
    widths = [max(_width(r[i]) for r in rows) for i in range(ncol)]
    total = sum(widths) + 3 * (ncol - 1)
    if total <= 44:
        def line(r):
            return " │ ".join(_pad(c, widths[i]) for i, c in enumerate(r)).rstrip()
        sep = "─┼─".join("─" * w for w in widths)
        body = [line(rows[0]), sep] + [line(r) for r in rows[1:]] if len(rows) > 1 else [line(rows[0])]
        return "<pre>" + esc("\n".join(body)) + "</pre>"
    head, data = rows[0], rows[1:]
    if not data:
        return "<b>" + esc(" · ".join(head)) + "</b>"
    if ncol == 2:
        return "\n".join(f"• <b>{esc(r[0])}</b> — {esc(r[1])}" for r in data)
    cards = []
    for r in data:
        lines = [f"<b>{esc(r[0])}</b>"] + [f"   {esc(head[i])}: {esc(r[i])}" for i in range(1, ncol) if r[i]]
        cards.append("\n".join(lines))
    return "\n\n".join(cards)


def md_to_html(text: str, legacy: bool = False) -> str:
    """Convert model-style Markdown to VALID Telegram HTML (always passes through sanitize_html).
    legacy=True treats *x* as bold and _x_ as italic (old Telegram Markdown strings)."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\x00", "")
    stash = []

    def keep(h: str) -> str:
        stash.append(h)
        return f"\x00{len(stash) - 1}\x00"

    def fence(m):
        lang, body = m.group(1), m.group(2).rstrip("\n")
        b = esc(body)
        if lang and re.fullmatch(r"[\w+#.-]+", lang):
            return keep(f'<pre><code class="language-{lang}">{b}</code></pre>')
        return keep(f"<pre>{b}</pre>")

    text = _FENCE.sub(fence, text)
    text = _TABLE.sub(lambda m: keep(_render_table(m.group(0))), text)
    text = _INLINE.sub(lambda m: keep(f"<code>{esc(m.group(2).strip())}</code>"), text)
    text = _ESCAPED.sub(lambda m: keep(esc(m.group(1))), text)
    text = esc(text)

    text = _BR.sub("\n", text)
    text = _RAW_TAG.sub(lambda m: f"<{m.group(1)}{_CANON.get(m.group(2).lower(), m.group(2).lower())}>", text)
    text = _HR.sub("──────────", text)
    text = _HEAD.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = _TASK.sub(lambda m: f"{m.group(1)}{'☑' if m.group(2) in 'xX' else '☐'} ", text)
    text = _BULLET.sub(lambda m: f"{m.group(1)}{'◦' if len(m.group(1).replace(chr(9), '    ')) >= 2 else '•'} ", text)
    text = _LINK.sub(lambda m: f'<a href="{m.group(2).replace(chr(34), "%22")}">{m.group(1)}</a>', text)
    if legacy:
        text = _L_BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", text)
        text = _L_ITALIC.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    else:
        text = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
        text = _ITALIC.sub(lambda m: f"<i>{m.group(1)}</i>", text)
        text = _ITALIC_U.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    text = _STRIKE.sub(lambda m: f"<s>{m.group(1)}</s>", text)
    text = _SPOILER.sub(lambda m: f"<tg-spoiler>{m.group(1)}</tg-spoiler>", text)

    # '> quote' lines (escaped to &gt; above) -> one <blockquote> per run
    lines, out, quote = text.split("\n"), [], []
    for ln in lines + [None]:
        qm = _QUOTE_LINE.match(ln) if ln is not None else None
        if qm:
            quote.append(qm.group(1))
            continue
        if quote:
            out.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
            quote = []
        if ln is not None:
            out.append(ln)
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # restore stashed fragments (nested stash refs can appear inside table cells / quotes)
    for _ in range(3):
        text = re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)
    return sanitize_html(text.strip("\n"))


# ─── Chunking (keeps code fences balanced, stays under 4096 after conversion) ─

def split_md(text: str, limit: int = 3000) -> list:
    lines = []
    for l in text.split("\n"):
        while len(l) > limit:
            cut = l.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit      # prefer a word boundary
            lines.append(l[:cut])
            l = l[cut:].lstrip(" ") if cut != limit else l[cut:]
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
        if line.strip().startswith(("```", "~~~")):
            if in_fence:
                in_fence, fence_open = False, ""
            else:
                in_fence, fence_open = True, line.strip()
    if cur.strip():
        chunks.append(cur)
    return chunks or [""]


def render_final(text: str, limit: int = 3000) -> list:
    """Model text -> list of valid Telegram-HTML chunks, each <= 4096 visible characters."""
    out = []
    for c in split_md(text, limit):
        h = md_to_html(c)
        if visible_len(h) > MAX - 200 and limit > 400:     # leave room for the footer
            out += render_final(c, limit // 2)
        elif h.strip():
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

def fmt_elapsed(sec: float) -> str:
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m {sec % 60:02d}s"
    return f"{sec // 3600}h {(sec % 3600) // 60:02d}m"


def _short(name: str, args: dict) -> str:
    """One-line description of a tool call for the status bubble."""
    args = args or {}
    if name == "shell_exec":
        cmd = str(args.get("cmd") or args.get("command") or "").replace("\n", " ")
        return "$ " + trunc(cmd, 44) if cmd else name
    if name in ("read_file", "edit_file", "file_op"):
        path = str(args.get("path") or "")
        return f"{name} {trunc(path.rsplit('/', 1)[-1], 30)}" if path else name
    if name in ("grep", "glob_files"):
        return f"{name} {trunc(str(args.get('pattern') or ''), 30)}".strip()
    if name == "web_search":
        return "search " + trunc(str(args.get("query") or ""), 34)
    if name == "update_task":
        return "update task.md"
    return name


_CHECK = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(.*)$", re.M)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\r")


def clean_ansi(text: str) -> str:
    """Strip ANSI escape sequences and carriage returns from terminal output."""
    if not text:
        return ""
    return _ANSI_RE.sub("", text)


def render_progress_bar(done: int, total: int, width: int = 10) -> str:
    """Render a visual ASCII/Unicode progress bar like [████░░░░░░] 40%."""
    if total <= 0:
        return f"[{'░' * width}] 0%"
    pct = min(1.0, max(0.0, done / total))
    filled = int(round(pct * width))
    empty = max(0, width - filled)
    return f"[{'█' * filled}{'░' * empty}] {int(pct * 100)}%"


class Status:
    def __init__(self, label: str, emoji: str):
        self.label, self.emoji = label, emoji
        self.t0 = time.time()
        self.steps = []              # one entry per tool call (batches included)
        self.log = []
        self.plan_done = self.plan_total = 0
        self.plan_next = ""

    def elapsed(self) -> float:
        return time.time() - self.t0

    def start(self, step, name, args=None):
        self.steps.append({"step": step, "name": name, "args": args or {}, "icon": "⏳",
                           "label": _short(name, args), "t0": time.time()})

    def finish(self, step, ok, name, out="", args=None):
        entry = next((e for e in self.steps if e["step"] == step and e["name"] == name and e["icon"] == "⏳"), None)
        secs = 0.0
        if entry:
            entry["icon"] = "✅" if ok else "❌"
            secs = time.time() - entry["t0"]
            args = entry["args"]
        self.log.append({"step": step, "tool": name, "args": args or {}, "ok": ok,
                         "secs": round(secs, 2), "output": out or ""})

    def set_plan(self, plan_text):
        """Progress from the agent's own task.md checklist."""
        if not plan_text:
            return
        items = _CHECK.findall(plan_text)
        if not items:
            return
        self.plan_total = len(items)
        self.plan_done = sum(1 for m, _ in items if m in "xX")
        nxt = next((t for m, t in items if m == " "), "")
        self.plan_next = re.sub(r"[*_`]", "", nxt).strip()

    def header(self) -> str:
        n = len(self.steps)
        tools = f" · 🔧 {n}" if n else ""
        return f"{self.emoji} <b>{esc(self.label)}</b> · ⏱ {fmt_elapsed(self.elapsed())}{tools}"

    def render(self, note: str = "Working…") -> str:
        items = self.steps[-6:]
        hidden = len(self.steps) - len(items)
        tl = "\n".join(f"{e['icon']} <code>{esc(e['label'])}</code>" for e in items)
        more = f"<i>+{hidden} earlier steps</i>\n" if hidden > 0 else ""
        plan = ""
        if self.plan_total:
            bar = render_progress_bar(self.plan_done, self.plan_total)
            plan = f"📋 {bar} (<b>{self.plan_done}/{self.plan_total}</b>)"
            if self.plan_next:
                plan += f"\n⏳ <b>Next:</b> <code>{esc(trunc(self.plan_next, 60))}</code>"
            plan += "\n\n"
        body = f"{more}{tl}\n\n" if tl else ""
        return f"{self.header()}\n\n{plan}{body}<i>{esc(note)}</i>"


def stream_view(answer_text: str, status: Status, think_text: str = "") -> str:
    """Streaming reply: compact header + the tail of the answer so far (or the model's reasoning)."""
    if not answer_text.strip() and think_text.strip():
        return f"{status.header()}\n\n{think_view(think_text)}"
    t = answer_text if len(answer_text) <= 2800 else "…" + answer_text[-2800:]
    return fit_html(f"{status.header()}\n\n{md_to_html(t)}")


def footer(status: Status) -> str:
    n = len(status.steps)
    tools = f" · 🔧 {n} step{'s' if n != 1 else ''}" if n else ""
    return f"\n\n<i>{status.emoji} {esc(status.label)} · ⏱ {fmt_elapsed(status.elapsed())}{tools}</i>"


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


# ─── Live Terminal & Dual-Layer Bubble Renderers ─────────────────────────────


def render_task_dashboard(
    title: str,
    plan_text: str = None,
    elapsed: float = 0.0,
    active_action: str = None,
    note: str = None
) -> str:
    """Main Task Bubble: checklist with [x] / [⏳] / [ ] and animated progress bar."""
    items = []
    done_count = 0
    if plan_text:
        raw_items = re.findall(r"^\s*-\s*\[([ xX])\]\s*(.+)$", plan_text, re.MULTILINE)
        total = len(raw_items)
        found_first_pending = False
        for mark, text in raw_items:
            is_done = mark in ("x", "X")
            clean_text = re.sub(r"[*_`]", "", text).strip()
            if is_done:
                done_count += 1
                items.append(f"✅ <code>{esc(clean_text)}</code>")
            elif not found_first_pending:
                found_first_pending = True
                items.append(f"⏳ <b>{esc(clean_text)}</b>")
            else:
                items.append(f"◻️ <i>{esc(clean_text)}</i>")
    else:
        total = 0

    header = f"📋 <b>{esc(title or 'Active Task')}</b> · ⏱ {fmt_elapsed(elapsed)}"
    bar = ""
    if total > 0:
        bar = f"{render_progress_bar(done_count, total)} (<b>{done_count}/{total}</b> completed)\n\n"

    checklist_str = "\n".join(items) if items else ""
    if checklist_str:
        checklist_str += "\n\n"

    action_str = f"⚡ <b>Action:</b> <code>{esc(active_action)}</code>\n" if active_action else ""
    note_str = f"💬 <i>{esc(note)}</i>\n" if note else ""

    content = f"{header}\n\n{bar}{checklist_str}{action_str}{note_str}".strip()
    return fit_html(content)


def render_command_bubble(
    cmd: str,
    elapsed: float,
    output: str,
    is_running: bool = True,
    exit_code: int = None,
    cwd: str = None
) -> str:
    """Command Bubble: live terminal output streamer with exit code badges."""
    clean_out = clean_ansi(output or "").strip()
    lines = clean_out.splitlines()

    # Viewport: keep last 16 lines for readability on mobile screens
    if len(lines) > 16:
        viewport = "\n".join(lines[-16:])
    else:
        viewport = "\n".join(lines) if lines else "(running quietly...)"

    cwd_hint = f" · 📁 <code>{esc(cwd)}</code>" if cwd else ""

    if is_running:
        status_line = f"⏱ {elapsed:.1f}s · ⏳ <i>Running…</i>{cwd_hint}"
    else:
        if exit_code == 0:
            status_line = f"✅ <b>Done in {elapsed:.1f}s</b> (exit 0){cwd_hint}"
        elif exit_code is not None:
            status_line = f"❌ <b>Failed in {elapsed:.1f}s</b> (exit {exit_code}){cwd_hint}"
        else:
            status_line = f"⏹ <b>Stopped after {elapsed:.1f}s</b>{cwd_hint}"

    body = f"🖥 <code>$ {esc(cmd)}</code>\n{status_line}\n\n<pre><code>{esc(viewport)}</code></pre>"
    return fit_html(body)


def render_shell_summary(cwd: str, commands: list, duration: float) -> str:
    """Consolidated Shell Session Summary Card shown upon exiting shell mode."""
    total_cmds = len(commands)
    header = f"🖥 <b>Shell Session Closed</b>\n⏱ Active: {fmt_elapsed(duration)} · 📁 <code>{esc(cwd)}</code>\n"
    
    if not commands:
        body = "\n<i>No commands were executed.</i>\n"
    else:
        lines = []
        for c in commands[-12:]:  # Show up to 12 most recent commands in summary
            cmd = c.get("cmd", "")
            code = c.get("exit_code", 0)
            took = c.get("took", 0.0)
            badge = "✅" if code == 0 else f"❌({code})"
            lines.append(f"{badge} <code>$ {esc(cmd)}</code> <i>({took:.1f}s)</i>")
        
        overflow = total_cmds - len(lines)
        if overflow > 0:
            lines.insert(0, f"<i>+{overflow} earlier commands…</i>")
        body = f"\n⚡ <b>Commands Executed ({total_cmds}):</b>\n" + "\n".join(lines) + "\n"

    footer = "\n🤖 <i>HARVIS is back online. How can I help you, Harold?</i>"
    return fit_html(header + body + footer)
