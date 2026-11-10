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

# Projects registry
PROJECTS_REGISTRY = HARVIS_DIR / "projects.json"


def get_projects_registry() -> dict:
    """Return projects dict mapping project keys to their metadata."""
    if not PROJECTS_REGISTRY.exists():
        return {}
    try:
        import json
        with open(PROJECTS_REGISTRY, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def register_project(name: str, path: str, ptype: str = "general", description: str = "") -> bool:
    """Register or update a known project path."""
    import json
    data = get_projects_registry()
    data[name] = {
        "path": str(Path(path).expanduser().resolve()),
        "type": ptype,
        "description": description or f"{name} project"
    }
    try:
        with open(PROJECTS_REGISTRY, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        return False

# State & persistence
HARVIS_SESSIONS = HARVIS_DIR / "sessions"
DB_PATH = HARVIS_SESSIONS / "harvis.db"
BUSY_FILE = HARVIS_SESSIONS / "busy_state.json"
STATE_FILE = HARVIS_DIR / "STATE.md"

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
