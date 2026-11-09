"""Telegram HTML must be valid for ANY model output: one bad tag makes Telegram reject the whole message."""
import asyncio
import os
import random
import sys
from html.parser import HTMLParser

import pytest

pytest.importorskip("telegram")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import telegram_bot.tg_io as tio  # noqa: E402
import telegram_bot.tg_ui as ui  # noqa: E402
from telegram.error import BadRequest, NetworkError, RetryAfter  # noqa: E402

ALLOWED = {"b", "i", "u", "s", "a", "code", "pre", "blockquote", "tg-spoiler"}


class Validator(HTMLParser):
    """Enforces what Telegram's HTML parser enforces."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED:
            self.errors.append(f"tag not allowed: {tag}")
        if "code" in self.stack:
            self.errors.append(f"<{tag}> inside <code>")
        if "pre" in self.stack and tag != "code":
            self.errors.append(f"<{tag}> inside <pre>")
        if tag == "blockquote" and "blockquote" in self.stack:
            self.errors.append("nested blockquote")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"bad nesting at </{tag}> (stack={self.stack})")
        else:
            self.stack.pop()

    def handle_data(self, d):
        if "<" in d or ">" in d or "&" in d:
            self.errors.append(f"raw special char in text: {d!r}")

    def handle_entityref(self, name):
        if name not in ("amp", "lt", "gt", "quot"):
            self.errors.append(f"unknown entity &{name};")


def problems(h: str):
    v = Validator()
    v.feed(h)
    v.close()
    if v.stack:
        v.errors.append(f"unclosed: {v.stack}")
    if ui.visible_len(h) > 4096:
        v.errors.append(f"too long: {ui.visible_len(h)}")
    return v.errors


# ── sanitizer ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expect", [
    ("<b>a<i>b</b>c</i>", "<b>a<i>b</i></b><i>c</i>"),
    ("<b>unclosed", "<b>unclosed</b>"),
    ("stray </b> closer", "stray  closer"),
    ("<pre><b>x</b></pre>", "<pre>x</pre>"),
    ("<code><i>x</i></code>", "<code>x</code>"),
    ("<blockquote>a<blockquote>b</blockquote></blockquote>", "<blockquote>ab</blockquote>"),
    ("1 < 2 && 3 > 2", "1 &lt; 2 &amp;&amp; 3 &gt; 2"),
    ("<script>alert(1)</script>", "&lt;script&gt;alert(1)&lt;/script&gt;"),
    ('<a href="javascript:x">l</a>', "l"),
    ('<a href="https://x.y/?a=1&amp;b=2">l</a>', '<a href="https://x.y/?a=1&amp;b=2">l</a>'),
    ("<b></b>text", "text"),
])
def test_sanitize_cases(raw, expect):
    assert ui.sanitize_html(raw) == expect


def test_bold_across_a_code_block_is_repaired():
    h = ui.sanitize_html("<b>intro <pre>code</pre> outro</b>")
    assert not problems(h) and "code" in h and "intro" in h and "outro" in h


def test_truncation_never_cuts_a_tag_and_counts_visible_text():
    h = "<b>" + "x" * 5000 + "</b>"
    out = ui.fit_html(h, 4096)
    assert not problems(out) and ui.visible_len(out) <= 4096 and out.endswith("…</b>")


# ── markdown conversion ──────────────────────────────────────────────────────

def test_common_markdown_renders_as_expected():
    out = ui.md_to_html("# T\n**b** *i* ~~s~~ `c<d` [l](https://x.y/a_(b))\n- one\n  - two\n- [x] done\n> q\n---")
    assert "<b>T</b>" in out and "<b>b</b>" in out and "<i>i</i>" in out and "<s>s</s>" in out
    assert "<code>c&lt;d</code>" in out and 'href="https://x.y/a_(b)"' in out
    assert "• one" in out and "◦ two" in out and "☑ done" in out
    assert "<blockquote>q</blockquote>" in out and "──────────" in out
    assert not problems(out)


def test_narrow_table_is_an_aligned_grid_and_wide_table_becomes_cards():
    narrow = ui.md_to_html("| a | b |\n|---|---|\n| 1 | 2 |")
    assert narrow.startswith("<pre>") and "│" in narrow and "---" not in narrow
    wide = ui.md_to_html("| Name | Description of the thing | Status | Owner |\n|--|--|--|--|\n"
                         "| Alpha | A very long description that would wrap badly | OK | Sam |")
    assert "<pre>" not in wide and "<b>Alpha</b>" in wide and "Status: OK" in wide
    assert not problems(narrow) and not problems(wide)


def test_snake_case_and_math_are_not_mangled():
    out = ui.md_to_html("use my_var_name and 2 * 3 * 4 and file_name_here")
    assert "my_var_name" in out and "<i>" not in out and "2 * 3 * 4" in out


def test_escaped_markdown_stays_literal():
    assert "*lit*" in ui.md_to_html(r"\*lit\*") and "<i>" not in ui.md_to_html(r"\*lit\*")


def test_model_html_tags_are_honoured_or_neutralised_never_fatal():
    out = ui.md_to_html("<b>ok</b> <br> <div>no</div> <i>unclosed")
    assert not problems(out) and "<b>ok</b>" in out and "&lt;div&gt;" in out


def test_long_answer_chunks_are_each_valid_and_lose_nothing():
    para = "Word **bold** and `code` and [link](https://e.com/x) and plain text here. " * 4
    text = "\n\n".join([f"## Section {i}\n\n{para}\n\n```python\n" + "\n".join(f"x{j} = {j}" for j in range(40)) + "\n```"
                        for i in range(40)])
    chunks = ui.render_final(text)
    assert len(chunks) > 3
    for c in chunks:
        assert not problems(c)
    joined = " ".join(ui.strip_tags(c) for c in chunks)
    assert "Section 0" in joined and "Section 39" in joined and joined.count("x39 = 39") == 40


def test_one_huge_line_is_still_delivered_validly():
    chunks = ui.render_final("a" * 20000)
    assert all(not problems(c) for c in chunks) and sum(ui.visible_len(c) for c in chunks) >= 20000


def test_footer_and_status_are_valid():
    st = ui.Status("Deep", "🧠")
    st.set_plan("- [x] a\n- [ ] do <b>the</b> thing & more")
    st.start(1, "shell_exec", {"cmd": "echo '<x>' && ls"})
    assert not problems(st.render("Working…")) and not problems(ui.footer(st))
    assert not problems(ui.stream_view("**partial *text", st)) and not problems(ui.stream_view("", st, "thinking <b"))


# ── fuzz ─────────────────────────────────────────────────────────────────────

TOKENS = ["**", "*", "_", "__", "`", "```", "```py\n", "~~~", "\n", "\n\n", "# ", "- ", "> ", "1. ", "---\n", "~~", "||",
          "| a | b |\n|---|---|\n| 1 | 2 |\n", "<b>", "</b>", "<i>", "</i>", "<pre>", "</pre>", "<code>", "</code>",
          "<a href='http://x'>", "</a>", "<blockquote>", "</blockquote>", "<", ">", "&", "&amp;", "&nbsp;", "\\*", "\\",
          "[x](http://a.b/c_(d))", "[y](javascript:z)", "word", " ", "日本語", "😀", "<think>", "</think>", "\x00",
          "<br>", "<br/>", "<<b>>", "&#x41;", "\t", "    ", "- [ ] ", "- [x] "]


def test_fuzz_every_output_is_valid_telegram_html():
    rnd = random.Random(1234)
    for n in range(4000):
        text = "".join(rnd.choice(TOKENS) for _ in range(rnd.randint(1, 60)))
        for fn in (ui.md_to_html, lambda t: ui.md_to_html(t, legacy=True), ui.sanitize_html):
            errs = problems(fn(text))
            assert not errs, f"case {n}: {errs}\ninput: {text!r}"
        for chunk in ui.render_final(text * 3):
            assert not problems(chunk), f"render_final case {n}: {problems(chunk)}\ninput: {text!r}"


# ── ChatIO: rate limiting, coalescing, flood control ─────────────────────────

class VClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    async def sleep(self, d):
        self.t += max(d, 0)
        await asyncio.sleep(0)


class Target:
    def __init__(self, clock, faults=None):
        self.clock, self.faults, self.calls, self.message_id = clock, list(faults or []), [], 7

    def _maybe_fault(self):
        if self.faults:
            f = self.faults.pop(0)
            if f:
                raise f

    async def reply_text(self, text, **kw):
        self.calls.append(("send", self.clock.t, text, kw.get("parse_mode")))
        self._maybe_fault()
        return Target(self.clock)

    async def edit_text(self, text, **kw):
        self.calls.append(("edit", self.clock.t, text, kw.get("parse_mode")))
        self._maybe_fault()


def mk(gap=1.0, **kw):
    c = VClock()
    return c, tio.ChatIO(gap=gap, margin=0.5, now=c.now, sleep=c.sleep, **kw)


def test_calls_are_spaced_by_the_gap():
    async def go():
        c, io = mk(gap=1.0)
        t = Target(c)
        for i in range(5):
            await io.send(t, f"m{i}")
        times = [x[1] for x in t.calls]
        assert all(b - a >= 1.0 - 1e-9 for a, b in zip(times, times[1:]))
    asyncio.run(go())


def test_edit_burst_is_coalesced_and_newest_text_wins():
    async def go():
        c, io = mk(gap=1.0)
        t = Target(c)
        for i in range(60):
            io.edit(t, f"text {i}")
        await io.drain()
        assert len(t.calls) <= 2 and t.calls[-1][2] == "text 59"
    asyncio.run(go())


def test_retry_after_waits_then_retries_the_same_call_once():
    async def go():
        c, io = mk(gap=1.0)
        t = Target(c, faults=[RetryAfter(5)])
        res = await io.send(t, "hello")
        assert res is not None and len(t.calls) == 2 and c.t >= 5.5
    asyncio.run(go())


def test_bad_request_is_never_retried_as_if_it_were_a_network_blip():
    """PTB's BadRequest subclasses NetworkError. Retrying it burned minutes of backoff."""
    async def go():
        c, io = mk()
        t = Target(c, faults=[BadRequest("Message is too long")])
        assert await io.send(t, "x", patient=True) is None
        assert len(t.calls) == 1 and c.t < 1
    asyncio.run(go())


def test_parse_error_retries_once_as_plain_text():
    async def go():
        c, io = mk()
        t = Target(c, faults=[BadRequest("Can't parse entities: can't find end tag")])
        assert await io.send(t, "<b>hi</b>") is not None
        assert [x[3] for x in t.calls] == [tio.HTML, None] and "<" not in t.calls[1][2]
    asyncio.run(go())


def test_network_errors_are_retried_with_backoff():
    async def go():
        c, io = mk()
        t = Target(c, faults=[NetworkError("boom"), NetworkError("boom")])
        assert await io.send(t, "x") is not None and len(t.calls) == 3
    asyncio.run(go())


def test_not_modified_and_vanished_messages_are_handled_quietly():
    async def go():
        c, io = mk(gap=0.1)
        t = Target(c, faults=[BadRequest("Message is not modified")])
        await io.edit_now(t, "same")
        t2 = Target(c, faults=[BadRequest("Message to edit not found")])
        await io.edit_now(t2, "a")
        n = len(t2.calls)
        io.edit(t2, "b")                      # known to be gone: no further API calls
        await io.drain()
        assert len(t2.calls) == n
    asyncio.run(go())


def test_malformed_html_is_sanitized_before_it_leaves():
    async def go():
        c, io = mk()
        t = Target(c)
        await io.send(t, "<b>a<i>b</b></i> <foo> & x")
        assert not problems(t.calls[0][2])
    asyncio.run(go())


def test_progress_bar_and_clean_ansi():
    assert ui.render_progress_bar(0, 10) == "[░░░░░░░░░░] 0%"
    assert ui.render_progress_bar(5, 10) == "[█████░░░░░] 50%"
    assert ui.render_progress_bar(10, 10) == "[██████████] 100%"
    
    ansi_dirty = "\x1b[32mSuccess\x1b[0m\r\nDone"
    assert ui.clean_ansi(ansi_dirty) == "Success\nDone"


def test_dual_layer_renderers_produce_valid_telegram_html():
    # 1. Command bubble
    bubble = ui.render_command_bubble("npm install", 2.3, "added 42 packages", is_running=False, exit_code=0, cwd="/home/harold/project")
    assert not problems(bubble)
    assert "exit 0" in bubble

    # 2. Task dashboard
    plan = "- [x] Setup environment\n- [ ] Install deps\n- [ ] Run build"
    dash = ui.render_task_dashboard("Build Web App", plan_text=plan, elapsed=12.5, active_action="npm install")
    assert not problems(dash)
    assert "1/3 completed" in dash

    # 3. Shell summary card
    history = [
        {"cmd": "ls -la", "exit_code": 0, "took": 0.1, "snippet": "total 4"},
        {"cmd": "git status", "exit_code": 0, "took": 0.2, "snippet": "clean"},
    ]
    summary = ui.render_shell_summary("/home/harold", history, 45.2)
    assert not problems(summary)
    assert "Shell Session Closed" in summary
    assert "Commands Executed (2)" in summary


def test_status_render_includes_progress_bar_when_plan_present():
    s = ui.Status("Default", "🛡️")
    s.set_plan("- [x] Step 1\n- [ ] Step 2")
    rendered = s.render("Thinking…")
    assert not problems(rendered)
    assert "1/2" in rendered
    assert "█" in rendered

