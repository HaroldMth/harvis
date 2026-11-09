"""Central path constants for HARVIS.

All runtime state lives under ~/.harvis/ so the repo stays pure source code.
Import from here — never hardcode paths elsewhere.
"""

from pathlib import Path

# ── Root ──────────────────────────────────────────────────────────────────────
HARVIS_DIR = Path.home() / ".harvis"

# HARVIS's own working home — builds, downloads, scratch, projects
HARVIS_HOME = HARVIS_DIR / "home"
HARVIS_PROJECTS = HARVIS_HOME / "projects"
HARVIS_DOWNLOADS = HARVIS_HOME / "downloads"
HARVIS_TMP = HARVIS_HOME / "tmp"

# State & persistence
HARVIS_SESSIONS = HARVIS_DIR / "sessions"
DB_PATH = HARVIS_SESSIONS / "harvis.db"
BUSY_FILE = HARVIS_SESSIONS / "busy_state.json"

# Logs
HARVIS_LOGS = HARVIS_DIR / "logs"

# Plugins (future)
HARVIS_PLUGINS = HARVIS_DIR / "plugins"

# Repo root (source code — read only at runtime)
REPO_ROOT = Path(__file__).resolve().parent.parent


def ensure_dirs():
    """Create all ~/.harvis subdirectories if they don't exist yet."""
    for d in (
        HARVIS_HOME,
        HARVIS_PROJECTS,
        HARVIS_DOWNLOADS,
        HARVIS_TMP,
        HARVIS_SESSIONS,
        HARVIS_LOGS,
        HARVIS_PLUGINS,
    ):
        d.mkdir(parents=True, exist_ok=True)
