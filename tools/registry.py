from tools.browser import open_app, open_url, close_app
from tools.system import shell_exec, file_op
from tools.info import system_info
from tools.desktop import (
    web_search, fetch_url, play_song, notify, clipboard, volume_control, list_dir,
    brightness_control, media_control, screenshot,
)
from tools.timer import timer
from tools.code import CODE_TOOLS_SCHEMA, CODE_TOOLS_DISPATCH
from tools.browser_fetch import browser_fetch
from tools.eyes import (
    desktop_look,
    desktop_click_element,
    desktop_click,
    desktop_type,
    desktop_press_keys,
)
from db.store import log_tool_call

import json
import os
import sys
import importlib.util
import re
import logging

logger = logging.getLogger("harvis.registry")

# ------------------------------------------------------------------
# Built-in tools schema
# ------------------------------------------------------------------

TOOLS_SCHEMA = [
    {
        "name": "close_app",
        "description": "Close, terminate, or kill running desktop applications by name (e.g. chrome, dolphin, settings, firefox, code, all). Use this whenever Harold asks to kill, close, or stop an application.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the app(s) to close (e.g. chrome, dolphin, settings, firefox, code, all)"
                }
            },
            "required": ["name"]
        }
    },
    {
        "name": "open_app",
        "description": "Launch an application on the computer. To open a website in a browser, pass both name (browser/app) and url. Site shortcuts like youtube, google, github, gmail are supported as url values.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Application or browser name (e.g. chrome, firefox, spotify, code)"
                },
                "url": {
                    "type": "string",
                    "description": "URL or site name to open in the browser (e.g. youtube, https://github.com). Required when opening websites."
                }
            },
            "required": ["name"]
        }
    },
    {
        "name": "open_url",
        "description": "Open a website in a browser. Supports site shortcuts (youtube, google, github, etc.) and full URLs. Optionally specify which browser to use.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL or site name to open (e.g. youtube, google.com, https://github.com)"
                },
                "browser": {
                    "type": "string",
                    "description": "Optional browser name (e.g. chrome, firefox). Uses default browser if omitted."
                }
            },
            "required": ["url"]
        }
    },
    {
        "name": "shell_exec",
        "description": "Executes a shell command on the local computer. For package installs, builds, or long scripts, specify a custom timeout in seconds (e.g. 60 or 120). For continuous or background services/daemons (dev servers, GUI apps, watchers), set is_daemon=true to detach immediately without blocking.",
        "parameters": {
            "type": "object",
            "properties": {
                "cmd": {
                    "type": "string",
                    "description": "The shell command to run (e.g. ls -la, npm install, cargo build)"
                },
                "is_daemon": {
                    "type": "boolean",
                    "description": "Set to true for long-running servers, watchers, GUI applications, or continuous commands so they run in the background without blocking."
                },
                "timeout": {
                    "type": "integer",
                    "description": "Optional wait time in seconds (default 120, max 1800). Commands still running at the limit are killed. Set higher for big installs, builds or test suites."
                },
                "cwd": {
                    "type": "string",
                    "description": "Directory to run in. Defaults to the active project directory when a session has one."
                }
            },
            "required": ["cmd"]
        }
    },
    {
        "name": "file_op",
        "description": "Performs file operations (read, write, move, delete) on the local computer",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "The file action to perform",
                    "enum": ["read", "write", "move", "delete"]
                },
                "path": {
                    "type": "string",
                    "description": "The path to the file or directory"
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file (only for 'write' action)"
                },
                "destination": {
                    "type": "string",
                    "description": "The destination path (only for 'move' action)"
                }
            },
            "required": ["action", "path"]
        }
    },
    {
        "name": "list_dir",
        "description": "List files and directories at a given path",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path to list (defaults to current directory)"
                }
            }
        }
    },
    {
        "name": "system_info",
        "description": "CPU load, RAM usage, disk space, and the processes using the most CPU and memory. Use this for ram, storage, disk, memory, and top/consuming apps — do not use shell_exec for those.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "web_search",
        "description": "Search the web for current information, facts, news, definitions, or how-to answers. Also useful for weather if the weather tool is unavailable. Returns text results when available; use open_in_browser=true to open results in the browser.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query"
                },
                "open_in_browser": {
                    "type": "boolean",
                    "description": "If true, open search results directly in the browser instead of returning text"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of web results to return (1-10; default 5)"
                },
                "fetch_content": {
                    "type": "boolean",
                    "description": "Fetch readable text from the top public result and include it (default true). Set false for result snippets only."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "fetch_url",
        "description": "Fetch and extract readable text from a public web page. Use after web_search when more detail from a specific result is needed.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Complete public HTTP or HTTPS URL to fetch"
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum page text to return (500-30000; default 12000)"
                }
            },
            "required": ["url"]
        }
    },
    {
        "name": "browser_fetch",
        "description": "Fetch and read any webpage using a real headless Chromium browser with full JavaScript rendering. Use this instead of fetch_url when: the page requires JavaScript/React/Vue to render, the site has Cloudflare or anti-bot protection, the content is loaded dynamically (SPAs, infinite scroll), or fetch_url returns empty/partial content. Can extract full page text, links, or take a screenshot.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL to open in the headless browser (must start with http:// or https://)"
                },
                "extract": {
                    "type": "string",
                    "enum": ["text", "media", "links", "html", "screenshot_path"],
                    "description": "What to extract: 'text' (readable content, default), 'media' (all images, videos, audio, OG meta tags), 'links' (page URLs), 'html' (rendered DOM HTML), 'screenshot_path' (take PNG screenshot)"
                },
                "wait_for": {
                    "type": "string",
                    "enum": ["networkidle", "load", "domcontentloaded"],
                    "description": "When to consider page fully loaded: 'networkidle' (default, best for SPAs), 'load', 'domcontentloaded' (fastest)"
                },
                "wait_selector": {
                    "type": "string",
                    "description": "Optional CSS selector to wait for before reading (e.g. '#main-content', '.article-body')"
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters of text to return (default 12000, max 50000)"
                }
            },
            "required": ["url"]
        }
    },
    {
        "name": "play_song",
        "description": "Play a song by searching for a direct YouTube track first, then a Spotify track as a fallback, and opening the result in the default browser.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Song title, optionally with artist name"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "notify",
        "description": "Show a desktop notification to the user",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Notification title"
                },
                "message": {
                    "type": "string",
                    "description": "Notification body text"
                }
            },
            "required": ["title"]
        }
    },
    {
        "name": "clipboard",
        "description": "Read from or write to the system clipboard",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write"],
                    "description": "Whether to read or write the clipboard"
                },
                "content": {
                    "type": "string",
                    "description": "Text to copy (required for write action)"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "volume_control",
        "description": "Control system audio volume (get current level, set level, mute, or unmute)",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "set", "mute", "unmute"],
                    "description": "Volume action to perform"
                },
                "level": {
                    "type": "integer",
                    "description": "Volume level 0-100 (required for 'set' action)"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "brightness_control",
        "description": "Control screen brightness (get current level, set level, max, or min)",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "set", "max", "min"],
                    "description": "Brightness action to perform"
                },
                "level": {
                    "type": "integer",
                    "description": "Brightness level 0-100 (required for 'set' action)"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "timer",
        "description": "Set, cancel, or list countdown timers. When a timer finishes, a desktop notification is shown.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start", "cancel", "list"],
                    "description": "Timer action to perform"
                },
                "seconds": {
                    "type": "integer",
                    "description": "Duration in seconds (required for 'start')"
                },
                "label": {
                    "type": "string",
                    "description": "Optional label for the timer (shown in notification)"
                },
                "timer_id": {
                    "type": "string",
                    "description": "Timer id (required for 'cancel')"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "media_control",
        "description": "Control media playback (play, pause, next track, previous track, stop, or get status)",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play", "pause", "play_pause", "next", "previous", "stop", "status"],
                    "description": "Media action to perform"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "screenshot",
        "description": "Take a screenshot of the screen and save it as a PNG file",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Optional output file path (defaults to ~/Pictures/HARVIS-screenshot-<timestamp>.png)"
                },
                "copy_to_clipboard": {
                    "type": "boolean",
                    "description": "If true, copy the screenshot file path to the clipboard"
                }
            }
        }
    },
    {
        "name": "session_manager",
        "description": "Manage HARVIS conversational workspaces and task sessions. Use this to create a dedicated session for a new project/app, list active sessions, switch between sessions, or update the session title, active project directory, and notes.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "list", "switch", "update", "info"],
                    "description": "Action: 'create' (starts new session), 'list' (shows all user sessions), 'switch' (switches active session), 'update' (renames or updates active_dir/notes), 'info' (gets details)"
                },
                "session_id": {
                    "type": "string",
                    "description": "4-character session ID (e.g. 'b7x2', 'general') when switching, updating, or getting info"
                },
                "title": {
                    "type": "string",
                    "description": "Short descriptive 3-5 word title for the session (e.g. 'YouTube Downloader APK')"
                },
                "active_dir": {
                    "type": "string",
                    "description": "Root project directory path associated with this session (e.g. '/home/harold/youtube_downloader_app')"
                },
                "notes": {
                    "type": "string",
                    "description": "Brief summary of progress or key technical facts to remember for this session"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_look",
        "description": "Inspect Harold's desktop screen, open windows, and interactive GUI widgets. Use mode='windows' to list active and open application windows. Use mode='ui' to read the accessibility widget tree of the active window (buttons, text inputs, checkboxes). Use mode='ocr' to capture fullscreen and extract text via OCR.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["windows", "ui", "ocr"],
                    "description": "Inspection mode: 'windows' (fast window list), 'ui' (semantic AT-SPI widget hierarchy), 'ocr' (fullscreen OCR)"
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters to return (default: 4000)"
                }
            }
        }
    },
    {
        "name": "desktop_click_element",
        "description": "Click an interactive UI element by name in the active window using native AT-SPI actions. Fast and native — triggers the widget directly without needing screen coordinates. Use desktop_look(mode='ui') first to check exact element names.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name or text label of the button, link, or element to click (e.g. 'OK', 'Save', 'Cancel')"
                }
            },
            "required": ["name"]
        }
    },
    {
        "name": "desktop_click",
        "description": "Move the mouse cursor and click absolute screen coordinates (x, y) via ydotool on Wayland. Prefer desktop_click_element() first; use this for coordinate-based clicks on custom canvases or games.",
        "parameters": {
            "type": "object",
            "properties": {
                "x": {
                    "type": "integer",
                    "description": "Horizontal pixel coordinate from top-left (e.g. 500)"
                },
                "y": {
                    "type": "integer",
                    "description": "Vertical pixel coordinate from top-left (e.g. 350)"
                }
            },
            "required": ["x", "y"]
        }
    },
    {
        "name": "desktop_type",
        "description": "Type text into the currently focused desktop input field via ydotool. Ensure the target text box or window is focused before calling.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text string to type into the focused field"
                }
            },
            "required": ["text"]
        }
    },
    {
        "name": "desktop_press_keys",
        "description": "Send key combinations or special keystrokes on Wayland via ydotool. Accepts friendly key names (e.g. 'enter', 'tab', 'esc', 'backspace', 'up', 'down', 'ctrl+c', 'ctrl+v', 'alt+tab', 'super') or raw evdev codes (e.g. '28:1 28:0').",
        "parameters": {
            "type": "object",
            "properties": {
                "combo": {
                    "type": "string",
                    "description": "Key name or shortcut combo (e.g. 'enter', 'esc', 'tab', 'ctrl+c', 'ctrl+v') or raw evdev sequence"
                }
            },
            "required": ["combo"]
        }
    }
]

# ------------------------------------------------------------------
# Plugin auto-discovery
# ------------------------------------------------------------------

_PLUGIN_CACHE = None


def _load_plugins():
    """Auto-discover all plugins in tools/plugins/*.py."""
    global _PLUGIN_CACHE
    if _PLUGIN_CACHE is not None:
        return _PLUGIN_CACHE

    plugins = []
    plugins_dir = os.path.join(os.path.dirname(__file__), "plugins")

    if not os.path.isdir(plugins_dir):
        _PLUGIN_CACHE = plugins
        return plugins

    for filename in sorted(os.listdir(plugins_dir)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue

        filepath = os.path.join(plugins_dir, filename)
        mod_name = f"tools.plugins.{filename[:-3]}"

        try:
            spec = importlib.util.spec_from_file_location(mod_name, filepath)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)

            schema = getattr(mod, "PLUGIN_SCHEMA", None)
            handler = getattr(mod, "handler", None)

            if schema and handler:
                plugins.append({"schema": schema, "handler": handler, "module": mod_name, "module_obj": mod})
                plugin_name = schema[0].get('name') if isinstance(schema, list) else schema.get('name', filename[:-3])
                logger.info(f"Loaded plugin: {plugin_name}")
        except Exception as e:
            logger.warning(f"Failed to load plugin {filename}: {e}")

    _PLUGIN_CACHE = plugins
    return plugins


def invalidate_plugin_cache():
    """Force plugin reload on next call — useful after hot-reloads."""
    global _PLUGIN_CACHE
    _PLUGIN_CACHE = None


def _plugin_matcher(query: str, examples: list) -> bool:
    """Simple keyword-based matcher using plugin examples."""
    q = query.lower()
    for ex in examples:
        words = re.findall(r"[a-zA-Z]{3,}", ex.lower())
        if any(w in q for w in words):
            return True
    return False


def _get_plugin_schemas():
    """Return plugin schemas merged into the main TOOLS_SCHEMA list."""
    schemas = []
    for plugin in _load_plugins():
        s = plugin["schema"]
        items = s if isinstance(s, list) else [s]
        for sub in items:
            schemas.append({
                "name": sub.get("name"),
                "description": sub.get("description", ""),
                "parameters": sub.get("parameters", {"type": "object", "properties": {}}),
            })
    return schemas


def _try_plugin_dispatch(name: str, args: dict) -> dict:
    """Try to dispatch to a plugin by name. Returns None if not a plugin."""
    for plugin in _load_plugins():
        s = plugin["schema"]
        mod = plugin.get("module_obj")
        items = s if isinstance(s, list) else [s]
        schema_names = [sub.get("name") for sub in items]

        if name in schema_names:
            try:
                # Normalize args first
                args = _normalize_tool_args(name, args)

                # Check for explicit name_handler in module
                if mod:
                    specific_handler = getattr(mod, f"{name}_handler", None)
                    if specific_handler:
                        result = specific_handler(args)
                        if isinstance(result, str):
                            return {"success": True, "output": result}
                        return result

                # Always pass the full args dict to the handler
                result = plugin["handler"](args)
                if isinstance(result, str):
                    return {"success": True, "output": result}
                return result
            except Exception as e:
                logger.error(f"Plugin {name} error: {e}")
                return {"success": False, "error": str(e)}
    return None


def _try_plugin_match(query: str) -> tuple:
    """Check if any plugin should handle this query. Returns (name, args) or (None, None)."""
    for plugin in _load_plugins():
        examples = plugin["schema"].get("examples", [])
        if _plugin_matcher(query, examples):
            return (plugin["schema"].get("name"), {"query": query})
    return (None, None)


# ------------------------------------------------------------------
# Tool dispatch
# ------------------------------------------------------------------

_ARG_ALIASES = {
    "shell_exec": (("command", "cmd"), ("query", "cmd"), ("script", "cmd"), ("code", "cmd")),
    "open_url": (("uri", "url"), ("link", "url")),
    "web_search": (("q", "query"), ("search", "query")),
}


def _normalize_tool_args(name, args):
    """Accept common LLM argument shapes so dispatch does not TypeError."""
    if args is None:
        args = {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            return {"cmd": args} if name == "shell_exec" else {"query": args}
    if not isinstance(args, dict):
        return {}

    args = dict(args)
    for wrapper in ("parameters", "arguments", "input"):
        inner = args.get(wrapper)
        if isinstance(inner, dict):
            merged = dict(inner)
            for key, value in args.items():
                if key != wrapper:
                    merged[key] = value
            args = merged
            break

    for alt, canonical in _ARG_ALIASES.get(name, ()):
        if args.get(canonical) in (None, "") and alt in args:
            args[canonical] = args[alt]
    return args


def session_manager(action: str = "list", session_id: str = None, title: str = None, active_dir: str = None, notes: str = None, user_id: str = "telegram_default", **kw) -> dict:
    """Manages conversational workspaces and coding sessions for HARVIS."""
    from db.store import create_session, get_active_session, set_active_session, list_user_sessions, get_session_info, update_session
    
    act = (action or "list").lower().strip()
    if act == "create":
        sid = create_session(user_id=user_id, title=title, active_dir=active_dir)
        if active_dir and title:
            from brain.paths import register_project
            register_project(title.lower().replace(" ", "_"), active_dir, description=title)
        return {"success": True, "output": f"Created and switched to session `{sid}`: '{title or sid}'."}
    elif act == "list":
        sessions = list_user_sessions(user_id)
        return {"success": True, "output": json.dumps(sessions, indent=2)}
    elif act == "switch":
        if not session_id:
            return {"success": False, "error": "session_id required to switch."}
        set_active_session(user_id, session_id)
        info = get_session_info(session_id)
        return {"success": True, "output": f"Switched to session `{session_id}`: '{info.get('title') if info else session_id}'."}
    elif act in ("update", "rename"):
        target_sid = session_id or get_active_session(user_id)
        update_session(target_sid, title=title, active_dir=active_dir, notes=notes)
        if active_dir:
            from brain.paths import register_project
            proj_name = (title or target_sid).lower().replace(" ", "_")
            register_project(proj_name, active_dir, description=title or "")
        return {"success": True, "output": f"Updated session `{target_sid}`."}
    elif act == "info":
        target_sid = session_id or get_active_session(user_id)
        info = get_session_info(target_sid)
        return {"success": True, "output": json.dumps(info, indent=2)}
    else:
        return {"success": False, "error": f"Unknown action '{act}'. Valid actions: create, list, switch, update, info."}


TOOL_DISPATCH = {
    "close_app": close_app,
    "open_app": open_app,
    "open_url": open_url,
    "shell_exec": lambda cmd=None, command=None, timeout=None, **kw: shell_exec(cmd=cmd, command=command, timeout=timeout, **kw),
    "file_op": file_op,
    "list_dir": list_dir,
    "system_info": lambda *a, **kw: system_info(),
    "web_search": web_search,
    "fetch_url": fetch_url,
    "browser_fetch": browser_fetch,
    "play_song": play_song,
    "notify": notify,
    "clipboard": clipboard,
    "volume_control": volume_control,
    "brightness_control": brightness_control,
    "timer": timer,
    "media_control": media_control,
    "screenshot": screenshot,
    "session_manager": session_manager,
    "desktop_look": desktop_look,
    "desktop_click_element": desktop_click_element,
    "desktop_click": desktop_click,
    "desktop_type": desktop_type,
    "desktop_press_keys": desktop_press_keys,
}


TOOLS_SCHEMA.extend(CODE_TOOLS_SCHEMA)
TOOL_DISPATCH.update(CODE_TOOLS_DISPATCH)


def get_all_tools_schema():
    """Return built-in + plugin schemas for LLM tool calling."""
    return TOOLS_SCHEMA + _get_plugin_schemas()


def _dispatch_impl(name, args):
    """
    Looks up tool by name, runs it, logs the outcome, and returns a standard dict.
    Returns: { "success": bool, "output": str, "error": str, "status": str }
    """
    result = None
    status = "success"

    # Try plugin dispatch first
    plugin_result = _try_plugin_dispatch(name, args)
    if plugin_result is not None:
        result = plugin_result
    else:
        try:
            args = _normalize_tool_args(name, args)
            if name == "shell_exec" and not args.get("cmd"):
                result = {
                    "success": False,
                    "error": "shell_exec requires cmd (the command string). For RAM, disk, or top processes use system_info with no arguments.",
                }
                status = "failed"
            else:
                handler = TOOL_DISPATCH.get(name)
                if handler:
                    result = handler(**args)
                else:
                    result = {"success": False, "error": f"Tool '{name}' not found."}
                    status = "failed"
        except TypeError as e:
            result = {"success": False, "error": f"Invalid arguments for tool '{name}': {e}"}
            status = "failed"
        except Exception as e:
            result = {"success": False, "error": str(e)}
            status = "failed"

    # Tools sometimes return None or a bare string; never let that crash the caller.
    if not isinstance(result, dict):
        result = {"success": True, "output": "(no output)" if result is None else str(result)}

    if result.get("status") == "denied":
        status = "denied"
    elif not result.get("success"):
        status = "failed"

    try:
        log_tool_call(
            tool_name=name,
            args=args,
            result=result.get("output") or result.get("error") or "",
            status=status
        )
    except Exception:
        pass  # a locked/unwritable log DB must never fail the tool that already ran

    return {
        "success": result.get("success", False),
        "output": result.get("output", ""),
        "error": result.get("error", ""),
        "status": status
    }


def dispatch(name, args):
    """Crash-proof wrapper: whatever happens inside a tool becomes a failed result the agent can read."""
    try:
        res = _dispatch_impl(name, args)
    except BaseException as e:  # noqa: BLE001 - includes SystemExit from misbehaving tools
        if isinstance(e, KeyboardInterrupt):
            raise
        res = {"success": False, "output": "", "error": f"Tool '{name}' crashed: {type(e).__name__}: {e}", "status": "failed"}
    return res
