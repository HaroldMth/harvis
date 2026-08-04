from tools.browser import open_app, open_url
from tools.system import shell_exec, file_op
from tools.info import system_info
from tools.desktop import (
    web_search, fetch_url, play_song, notify, clipboard, volume_control, list_dir,
    brightness_control, media_control, screenshot,
)
from tools.timer import timer
from db.store import log_tool_call

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
        "description": "Executes a shell command on the local computer",
        "parameters": {
            "type": "object",
            "properties": {
                "cmd": {
                    "type": "string",
                    "description": "The shell command to run (e.g. ls -la, neofetch)"
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
        "description": "Retrieves basic system information like CPU load, RAM usage, and disk space",
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
                plugins.append({"schema": schema, "handler": handler, "module": mod_name})
                logger.info(f"Loaded plugin: {schema.get('name', filename[:-3])}")
        except Exception as e:
            logger.warning(f"Failed to load plugin {filename}: {e}")

    _PLUGIN_CACHE = plugins
    return plugins


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
        schemas.append({
            "name": s.get("name"),
            "description": s.get("description", ""),
            "parameters": s.get("parameters", {"type": "object", "properties": {}}),
        })
    return schemas


def _try_plugin_dispatch(name: str, args: dict) -> dict:
    """Try to dispatch to a plugin by name. Returns None if not a plugin."""
    for plugin in _load_plugins():
        if plugin["schema"].get("name") == name:
            try:
                query = args.get("query", "") if isinstance(args, dict) else str(args)
                result = plugin["handler"](query)
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

TOOL_DISPATCH = {
    "open_app": open_app,
    "open_url": open_url,
    "shell_exec": shell_exec,
    "file_op": file_op,
    "list_dir": list_dir,
    "system_info": lambda **_: system_info(),
    "web_search": web_search,
    "fetch_url": fetch_url,
    "play_song": play_song,
    "notify": notify,
    "clipboard": clipboard,
    "volume_control": volume_control,
    "brightness_control": brightness_control,
    "timer": timer,
    "media_control": media_control,
    "screenshot": screenshot,
}


def get_all_tools_schema():
    """Return built-in + plugin schemas for LLM tool calling."""
    return TOOLS_SCHEMA + _get_plugin_schemas()


def dispatch(name, args):
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
            if args is None:
                args = {}

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

    if result.get("status") == "denied":
        status = "denied"
    elif not result.get("success"):
        status = "failed"

    log_tool_call(
        tool_name=name,
        args=args,
        result=result.get("output") or result.get("error") or "",
        status=status
    )

    return {
        "success": result.get("success", False),
        "output": result.get("output", ""),
        "error": result.get("error", ""),
        "status": status
    }
