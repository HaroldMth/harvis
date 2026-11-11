"""
HARVIS Cross-Message Continuity & Working Memory Engine.

Tracks touched files, edited contents, recent commands, active background
daemons, and STATE.md across conversation turns so the model never suffers
from amnesia when the user sends follow-up requests.
"""

import os
import json
import time
import logging
from pathlib import Path

logger = logging.getLogger("harvis.continuity")

HARVIS_DIR = Path(os.environ.get("HARVIS_DIR", Path.home() / ".harvis"))
STATE_FILE = HARVIS_DIR / "STATE.md"
SESSIONS_DIR = HARVIS_DIR / "sessions"

MAX_INLINE_FILE_LINES = 150
MAX_RECENT_FILES = 4
MAX_RECENT_COMMANDS = 3


def _get_context_file(session_id: str) -> Path:
    s_dir = SESSIONS_DIR / (session_id or "general")
    s_dir.mkdir(parents=True, exist_ok=True)
    return s_dir / "working_context.json"


def load_working_context(session_id: str) -> dict:
    c_file = _get_context_file(session_id)
    if c_file.exists():
        try:
            with open(c_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.debug(f"Failed to read working_context.json for {session_id}: {e}")
    return {"recent_files": [], "recent_commands": [], "updated_at": 0}


def save_working_context(session_id: str, data: dict) -> None:
    c_file = _get_context_file(session_id)
    try:
        data["updated_at"] = time.time()
        with open(c_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save working_context.json for {session_id}: {e}")


def record_touched_file(session_id: str, path: str, action: str = "edit") -> None:
    """Record a file created, edited, or read in the current session."""
    if not path or not isinstance(path, str):
        return
    abs_path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(abs_path) or os.path.isdir(abs_path):
        return

    ctx = load_working_context(session_id)
    recents = ctx.get("recent_files", [])

    # Filter out duplicate entries for this file
    recents = [r for r in recents if r.get("path") != abs_path]

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        line_count = len(lines)
    except Exception:
        line_count = 0

    entry = {
        "path": abs_path,
        "name": os.path.basename(abs_path),
        "action": action,
        "line_count": line_count,
        "timestamp": time.time(),
    }

    recents.insert(0, entry)
    ctx["recent_files"] = recents[:MAX_RECENT_FILES]
    save_working_context(session_id, ctx)


def record_command_action(session_id: str, cmd: str, exit_code: int = 0, output_summary: str = "") -> None:
    """Record a shell command executed in the current session."""
    if not cmd or not isinstance(cmd, str):
        return
    ctx = load_working_context(session_id)
    cmds = ctx.get("recent_commands", [])

    preview = (output_summary or "").strip()
    if len(preview) > 300:
        preview = preview[:150] + " ... " + preview[-150:]

    entry = {
        "cmd": cmd.strip(),
        "exit_code": exit_code,
        "output": preview,
        "timestamp": time.time(),
    }
    cmds.insert(0, entry)
    ctx["recent_commands"] = cmds[:MAX_RECENT_COMMANDS]
    save_working_context(session_id, ctx)


def build_working_memory_prompt(session_id: str, active_dir: str = None) -> str:
    """
    Constructs a rich, concise working memory block to be injected into
    the prompt header so the model knows active state across turns.
    """
    sections = []

    # 1. State from STATE.md
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state_text = f.read().strip()
            if state_text and len(state_text) > 10:
                sections.append(f"• Active Project State (from STATE.md):\n{state_text}")
        except Exception:
            pass

    # 2. Context from working_context.json
    ctx = load_working_context(session_id)
    recent_files = ctx.get("recent_files", [])

    if recent_files:
        files_block = ["• Recent Files from Previous Rounds:"]
        for f_info in recent_files:
            f_path = f_info.get("path")
            if not f_path or not os.path.exists(f_path):
                continue
            name = f_info.get("name", os.path.basename(f_path))
            lines_n = f_info.get("line_count", 0)

            try:
                with open(f_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                raw_lines = content.splitlines()
                lines_n = len(raw_lines)

                # Determine file extension for markdown syntax block
                ext = Path(f_path).suffix.lstrip(".") or "text"

                if lines_n <= MAX_INLINE_FILE_LINES:
                    files_block.append(
                        f"  - [{name}] `{f_path}` ({lines_n} lines, {f_info.get('action', 'edited')}):\n"
                        f"```{ext}\n{content.strip()}\n```"
                    )
                else:
                    preview = "\n".join(raw_lines[:25])
                    files_block.append(
                        f"  - [{name}] `{f_path}` ({lines_n} lines, {f_info.get('action', 'edited')}):\n"
                        f"```{ext}\n{preview}\n... [remainder omitted — call read_file to inspect lines 26-{lines_n}]\n```"
                    )
            except Exception as read_err:
                files_block.append(f"  - `{f_path}` ({lines_n} lines) [Error reading content: {read_err}]")

        if len(files_block) > 1:
            sections.append("\n".join(files_block))

    # 3. Active Background Daemons & Servers
    try:
        from tools.system import get_all_background_procs, is_process_alive
        bg_procs = get_all_background_procs()
        active_daemons = []
        for pid, pdata in bg_procs.items():
            if is_process_alive(pid):
                cmd_str = pdata.get("cmd", f"PID {pid}")
                active_daemons.append(f"  - PID `{pid}`: <code>{cmd_str}</code> (Logs: `{pdata.get('log_file')}`)")
        if active_daemons:
            sections.append("• Active Background Daemons & Servers:\n" + "\n".join(active_daemons))
    except Exception:
        pass

    # 4. Recent Commands
    recent_cmds = ctx.get("recent_commands", [])
    if recent_cmds:
        cmd_block = ["• Recent Terminal Commands from Previous Rounds:"]
        for c in recent_cmds:
            status_str = "✅" if c.get("exit_code") == 0 else f"❌ (exit {c.get('exit_code')})"
            out_preview = f" -> {c.get('output')}" if c.get("output") else ""
            cmd_block.append(f"  - {status_str} `$ {c.get('cmd')}`{out_preview}")
        sections.append("\n".join(cmd_block))

    if not sections:
        return ""

    joined = "\n\n".join(sections)
    return (
        f"=================================================================\n"
        f"[HARVIS WORKING MEMORY & SESSION CONTINUITY]\n"
        f"(This context persists across messages. You ALREADY know these files and state.)\n\n"
        f"{joined}\n"
        f"================================================================="
    )
