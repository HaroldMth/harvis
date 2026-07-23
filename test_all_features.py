#!/usr/bin/env python3
"""Run through every HARVIS tool and report pass/fail."""

import os
import sys
import tempfile
import time
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.system import set_confirmation_callback
from tools.registry import dispatch, TOOLS_SCHEMA
from tools.browser import resolve_url, resolve_browser

PASS = 0
FAIL = 0
SKIP = 0
RESULTS = []


def record(name, ok, detail="", skipped=False):
    global PASS, FAIL, SKIP
    if skipped:
        SKIP += 1
        status = "SKIP"
    elif ok:
        PASS += 1
        status = "PASS"
    else:
        FAIL += 1
        status = "FAIL"
    RESULTS.append((status, name, detail))
    icon = {"PASS": "+", "FAIL": "x", "SKIP": "~"}[status]
    print(f"  [{icon}] {name}: {detail}")


def main():
    set_confirmation_callback(lambda _msg: True)

    print("=" * 60)
    print("HARVIS Feature Test Suite")
    print(f"Tools registered: {len(TOOLS_SCHEMA)}")
    print("=" * 60)

    # --- Browser resolution (no side effects) ---
    print("\n--- Browser / URL resolution ---")
    record("resolve_url(youtube)", resolve_url("youtube") == "https://www.youtube.com")
    record("resolve_url(https://...)", resolve_url("https://github.com") == "https://github.com")
    record("resolve_url(github.com)", resolve_url("github.com") == "https://github.com")
    record("resolve_browser(chrome)", resolve_browser("chrome") == "google-chrome-stable")
    record("resolve_browser(firefox)", resolve_browser("firefox") == "firefox")

    # --- system_info ---
    print("\n--- system_info ---")
    r = dispatch("system_info", {})
    record("system_info", r["success"], r["output"].split("\n")[0] if r["success"] else r["error"])

    # --- list_dir ---
    print("\n--- list_dir ---")
    r = dispatch("list_dir", {"path": os.path.dirname(__file__)})
    record("list_dir", r["success"] and "main.py" in r["output"], "listed project root")

    # --- shell_exec ---
    print("\n--- shell_exec ---")
    r = dispatch("shell_exec", {"cmd": "echo harvis-test-ok && uname -s"})
    record("shell_exec", r["success"] and "harvis-test-ok" in r["output"], r["output"].strip()[:60])

    # --- file_op: write, read, move, delete ---
    print("\n--- file_op ---")
    tmpdir = tempfile.mkdtemp(prefix="harvis_test_")
    f1 = os.path.join(tmpdir, "test.txt")
    f2 = os.path.join(tmpdir, "moved.txt")

    r = dispatch("file_op", {"action": "write", "path": f1, "content": "HARVIS test content"})
    record("file_op write", r["success"], r.get("output", r.get("error", "")))

    r = dispatch("file_op", {"action": "read", "path": f1})
    record("file_op read", r["success"] and "HARVIS test content" in r["output"])

    r = dispatch("file_op", {"action": "move", "path": f1, "destination": f2})
    record("file_op move", r["success"] and os.path.exists(f2))

    r = dispatch("file_op", {"action": "delete", "path": f2})
    record("file_op delete", r["success"])

    try:
        os.rmdir(tmpdir)
    except OSError:
        pass

    # --- notify ---
    print("\n--- notify ---")
    r = dispatch("notify", {"title": "HARVIS Test", "message": "Feature test running..."})
    record("notify", r["success"], r.get("output", r.get("error", "")))

    # --- clipboard ---
    print("\n--- clipboard ---")
    test_text = f"harvis-clipboard-{int(time.time())}"
    r = dispatch("clipboard", {"action": "write", "content": test_text})
    record("clipboard write", r["success"], r.get("output", r.get("error", "")))

    r = dispatch("clipboard", {"action": "read"})
    record("clipboard read", r["success"] and test_text in r["output"], r["output"][:40])

    # --- volume_control ---
    print("\n--- volume_control ---")
    r = dispatch("volume_control", {"action": "get"})
    record("volume_control get", r["success"], r["output"][:50])

    r = dispatch("volume_control", {"action": "mute"})
    record("volume_control mute", r["success"])

    r = dispatch("volume_control", {"action": "unmute"})
    record("volume_control unmute", r["success"])

    # --- web_search ---
    print("\n--- web_search ---")
    r = dispatch("web_search", {"query": "Linux CachyOS"})
    record("web_search", r["success"], r["output"][:80] if r["success"] else r["error"])

    r = dispatch("web_search", {"query": "Python programming language"})
    record("web_search (instant)", r["success"] and len(r["output"]) > 20, r["output"][:80] if r["success"] else r["error"])

    # --- brightness_control ---
    print("\n--- brightness_control ---")
    r = dispatch("brightness_control", {"action": "get"})
    record("brightness_control get", r["success"], r["output"][:50] if r["success"] else r["error"])

    r = dispatch("brightness_control", {"action": "set", "level": 60})
    record("brightness_control set", r["success"], r.get("output", r.get("error", "")))

    # --- timer ---
    print("\n--- timer ---")
    r = dispatch("timer", {"action": "start", "seconds": 2, "label": "test timer"})
    record("timer start", r["success"], r.get("output", r.get("error", "")))
    timer_id = None
    if r["success"]:
        match = re.search(r"\(id: (\w+)\)", r["output"])
        timer_id = match.group(1) if match else None

    r = dispatch("timer", {"action": "list"})
    record("timer list", r["success"], r["output"][:60] if r["success"] else r["error"])

    if timer_id:
        time.sleep(2.5)
        r = dispatch("timer", {"action": "list"})
        record("timer finished", r["success"] and "No active timers" in r["output"], "timer completed")

    r = dispatch("timer", {"action": "start", "seconds": 60, "label": "cancel test"})
    if r["success"]:
        match = re.search(r"\(id: (\w+)\)", r["output"])
        cancel_id = match.group(1) if match else None
        if cancel_id:
            r2 = dispatch("timer", {"action": "cancel", "timer_id": cancel_id})
            record("timer cancel", r2["success"], r2.get("output", r2.get("error", "")))

    # --- media_control ---
    print("\n--- media_control ---")
    r = dispatch("media_control", {"action": "status"})
    record("media_control status", r["success"] or "playerctl" in r.get("error", ""), r.get("output", r.get("error", ""))[:60])

    # --- screenshot ---
    print("\n--- screenshot ---")
    r = dispatch("screenshot", {})
    compositor_unavailable = "screen capture" in r.get("error", "").lower() or "compositor" in r.get("error", "").lower()
    record(
        "screenshot",
        r["success"],
        r.get("output", r.get("error", ""))[:80],
        skipped=compositor_unavailable,
    )

    # --- open_url / open_app (launch browser — side effect) ---
    print("\n--- open_url / open_app ---")
    r = dispatch("open_url", {"url": "github", "browser": "chrome"})
    record("open_url (github in chrome)", r["success"], r.get("output", r.get("error", "")))

    r = dispatch("open_app", {"name": "chrome", "url": "youtube"})
    record("open_app (youtube in chrome)", r["success"], r.get("output", r.get("error", "")))

    # --- timer via shell_exec (legacy fallback still works) ---
    print("\n--- timer (shell_exec legacy) ---")
    r = dispatch("shell_exec", {"cmd": "sleep 3 && notify-send 'HARVIS Timer' '3 second timer done!'"})
    record("timer shell_exec started", r["success"], "3s timer dispatched" if r["success"] else r["error"])

    # --- Summary ---
    print("\n" + "=" * 60)
    print(f"RESULTS: {PASS} passed, {FAIL} failed, {SKIP} skipped")
    print("=" * 60)

    if FAIL:
        print("\nFailed tests:")
        for status, name, detail in RESULTS:
            if status == "FAIL":
                print(f"  - {name}: {detail}")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
