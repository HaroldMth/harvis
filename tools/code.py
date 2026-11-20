"""Code tools: exact-match editing, ranged reads, search, and a failures-only test runner.

Whole-file rewrites through a clipped view are the main source of agent coding
errors. These tools let the agent change a few lines, read only what it needs,
and see only what failed.
"""

import fnmatch
import os
import re
import shutil
import subprocess

MAX_READ_LINES = 400
MAX_MATCHES = 200
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".harvis", "dist", "build", ".mypy_cache"}


def _p(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def read_file(path: str, start: int = 1, end: int = None, **_):
    """Read a file with line numbers. Defaults to the first 400 lines; use start/end to page."""
    fp = _p(path)
    if not os.path.isfile(fp):
        return {"success": False, "error": f"Not a file: {fp}"}
    try:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except Exception as e:
        return {"success": False, "error": str(e)}
    total = len(lines)
    start = max(1, int(start or 1))
    end = int(end) if end else min(total, start + MAX_READ_LINES - 1)
    end = min(end, total, start + MAX_READ_LINES - 1)
    body = "\n".join(f"{i}\t{lines[i - 1]}" for i in range(start, end + 1))
    more = f"\n[{total} lines total; showing {start}-{end}. Call again with start={end + 1} for more.]" if end < total else f"\n[{total} lines total]"
    return {"success": True, "output": body + more}


def edit_file(path: str, old_str: str, new_str: str = "", replace_all: bool = False, **_):
    """Replace exact text in a file. old_str must match exactly once unless replace_all is true."""
    fp = _p(path)
    if not os.path.isfile(fp):
        return {"success": False, "error": f"Not a file: {fp}"}
    if not old_str:
        return {"success": False, "error": "old_str must be non-empty."}
    try:
        with open(fp, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        return {"success": False, "error": str(e)}
    count = text.count(old_str)
    if count == 0:
        return {"success": False, "error": "old_str not found. Re-read the file (read_file) and copy the text exactly, including whitespace."}
    if count > 1 and not replace_all:
        return {"success": False, "error": f"old_str matches {count} places. Add surrounding lines to make it unique, or set replace_all=true."}
    new_text = text.replace(old_str, new_str) if replace_all else text.replace(old_str, new_str, 1)
    tmp = fp + ".harvis_tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new_text)
        shutil.copymode(fp, tmp)
        os.replace(tmp, fp)
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        return {"success": False, "error": str(e)}
    return {"success": True, "output": f"Edited {fp}: replaced {count if replace_all else 1} occurrence(s)."}


def grep(pattern: str, path: str = ".", glob: str = None, ignore_case: bool = False, **_):
    """Regex search across files. Returns file:line:text, capped at 200 matches."""
    root = _p(path)
    try:
        rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as e:
        return {"success": False, "error": f"Bad regex: {e}"}
    hits, files = [], ([root] if os.path.isfile(root) else None)
    if files is None:
        files = []
        for d, dirs, names in os.walk(root):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            for n in names:
                if not glob or fnmatch.fnmatch(n, glob):
                    files.append(os.path.join(d, n))
    for fp in files:
        try:
            if os.path.getsize(fp) > 2_000_000:
                continue
            with open(fp, "r", encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    if rx.search(line):
                        hits.append(f"{fp}:{i}:{line.rstrip()[:240]}")
                        if len(hits) >= MAX_MATCHES:
                            return {"success": True, "output": "\n".join(hits) + f"\n[capped at {MAX_MATCHES} matches]"}
        except (UnicodeDecodeError, OSError):
            continue
    return {"success": True, "output": "\n".join(hits) if hits else "No matches."}


def glob_files(pattern: str, path: str = ".", **_):
    """Find files by name pattern (e.g. '*.py', 'test_*.js'). Skips .git, node_modules, venvs."""
    root, out = _p(path), []
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for n in names:
            if fnmatch.fnmatch(n, pattern):
                out.append(os.path.join(d, n))
                if len(out) >= 500:
                    return {"success": True, "output": "\n".join(out) + "\n[capped at 500]"}
    return {"success": True, "output": "\n".join(out) if out else "No files matched."}


def _detect_test_cmd(cwd: str):
    has = lambda f: os.path.exists(os.path.join(cwd, f))
    if has("pytest.ini") or has("pyproject.toml") or has("tests") or has("setup.py"):
        return "python -m pytest -q -x --tb=short"
    if has("package.json"):
        return "npm test --silent"
    if has("Cargo.toml"):
        return "cargo test --quiet"
    if has("go.mod"):
        return "go test ./..."
    if has("Makefile"):
        return "make test"
    return None


_FAIL_HINT = re.compile(r"(FAIL|ERROR|Error|error:|assert|Traceback|panic|✗|✖|not ok|failed|passed)", re.I)


def run_tests(cmd: str = None, cwd: str = ".", timeout: int = 600, **_):
    """Run the project's tests and return a pass/fail verdict plus only the failure output."""
    cwd = _p(cwd)
    cmd = cmd or _detect_test_cmd(cwd)
    if not cmd:
        return {"success": False, "error": "Could not detect a test command. Pass cmd explicitly."}
    try:
        proc = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True,
                              timeout=max(5, min(1800, int(timeout))), executable="/bin/bash")
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Tests timed out after {timeout}s: {cmd}"}
    full = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    lines = full.splitlines()
    if proc.returncode == 0:
        tail = "\n".join(lines[-3:])
        return {"success": True, "output": f"PASSED (exit 0): {cmd}\n{tail}"}
    keep = [l for l in lines if _FAIL_HINT.search(l)]
    shown = "\n".join(keep[:80]) if keep else "\n".join(lines[-60:])
    return {"success": False, "output": f"FAILED (exit {proc.returncode}): {cmd}\n{shown}",
            "error": f"Tests failed (exit {proc.returncode})."}


CODE_TOOLS_SCHEMA = [
    {"name": "read_file",
     "description": "Read a text file with line numbers. Shows up to 400 lines; page with start/end. Prefer this over file_op for code.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "start": {"type": "integer", "description": "First line (1-based)."},
         "end": {"type": "integer", "description": "Last line."}}, "required": ["path"]}},
    {"name": "edit_file",
     "description": "Edit a file by replacing exact text. old_str must match exactly once (include surrounding lines to make it unique) unless replace_all is true. Use this instead of rewriting whole files.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "old_str": {"type": "string"}, "new_str": {"type": "string"},
         "replace_all": {"type": "boolean"}}, "required": ["path", "old_str", "new_str"]}},
    {"name": "grep",
     "description": "Regex search over files under a path. Returns file:line:text (max 200). Optional glob filter like '*.py'.",
     "parameters": {"type": "object", "properties": {
         "pattern": {"type": "string"}, "path": {"type": "string"}, "glob": {"type": "string"},
         "ignore_case": {"type": "boolean"}}, "required": ["pattern"]}},
    {"name": "glob_files",
     "description": "Find files by name pattern (e.g. '*.py') under a path.",
     "parameters": {"type": "object", "properties": {
         "pattern": {"type": "string"}, "path": {"type": "string"}}, "required": ["pattern"]}},
    {"name": "run_tests",
     "description": "Run the project's tests (auto-detects pytest/npm/cargo/go/make, or pass cmd) and return a pass/fail verdict with only the failure output.",
     "parameters": {"type": "object", "properties": {
         "cmd": {"type": "string"}, "cwd": {"type": "string"}, "timeout": {"type": "integer"}}}},
]

CODE_TOOLS_DISPATCH = {
    "read_file": read_file, "edit_file": edit_file, "grep": grep,
    "glob_files": glob_files, "run_tests": run_tests,
}
