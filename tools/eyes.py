"""
HARVIS Eyes — Desktop Vision & Wayland Automation.

Provides desktop perception via:
1. AT-SPI2 Accessibility Tree (fast, semantic UI inspection of GTK/Qt widgets)
2. KWin Wayland window targeting (via kdotool, with AT-SPI fallback)
3. Fullscreen / Window OCR (via spectacle + tesseract)
4. Wayland-compatible input synthesis (via ydotool)
"""

import os
import re
import shutil
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger("harvis.eyes")

MAX_OUTPUT_CHARS = 6000
TMP_SNAP_PATH = "/tmp/harvis_eyes_snap.png"

# Setup ydotool socket environment if not set
os.environ.setdefault("YDOTOOL_SOCKET", f"/run/user/{os.getuid()}/.ydotool_socket")

# Linux evdev input scan codes for common keys
KEYMAP = {
    "enter": "28:1 28:0",
    "return": "28:1 28:0",
    "esc": "1:1 1:0",
    "escape": "1:1 1:0",
    "tab": "15:1 15:0",
    "backspace": "14:1 14:0",
    "space": "57:1 57:0",
    "delete": "111:1 111:0",
    "up": "103:1 103:0",
    "down": "108:1 108:0",
    "left": "105:1 105:0",
    "right": "106:1 106:0",
    "home": "102:1 102:0",
    "end": "107:1 107:0",
    "pageup": "104:1 104:0",
    "pagedown": "109:1 109:0",
    "ctrl+c": "29:1 46:1 46:0 29:0",
    "ctrl+v": "29:1 47:1 47:0 29:0",
    "ctrl+a": "29:1 30:1 30:0 29:0",
    "ctrl+z": "29:1 44:1 44:0 29:0",
    "ctrl+s": "29:1 31:1 31:0 29:0",
    "ctrl+w": "29:1 17:1 17:0 29:0",
    "alt+f4": "56:1 62:1 62:0 56:0",
    "alt+tab": "56:1 15:1 15:0 56:0",
    "super": "125:1 125:0",
}


def _sh(cmd: str, timeout: int = 15) -> str:
    """Executes a shell command safely and returns captured stdout+stderr."""
    try:
        r = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return f"[timeout after {timeout}s: {cmd}]"
    except Exception as e:
        return f"[error executing {cmd}: {e}]"


def _get_atspi():
    """Dynamically imports and initializes AT-SPI."""
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        return Atspi
    except Exception as e:
        logger.warning(f"Failed to initialize Atspi: {e}")
        return None


def _walk_tree(node, out: list, depth: int = 0, max_items: int = 300, max_depth: int = 12):
    """Walks an AT-SPI node tree recursively to extract semantic widgets."""
    if len(out) >= max_items or depth > max_depth or node is None:
        return

    try:
        name = (node.get_name() or "").strip()
        role = (node.get_role_name() or "").strip()

        # Interesting roles that contain useful interaction information
        notable_roles = {
            "push button", "text", "entry", "link", "check box", "radio button",
            "combo box", "menu item", "page tab", "tree table", "list item",
            "slider", "scroll bar", "toggle button", "heading", "label", "dialog", "frame"
        }

        if name or role in notable_roles:
            indent = "  " * depth
            role_tag = f"[{role}]" if role else "[item]"
            line = f"{indent}{role_tag} {name}".rstrip()
            out.append(line)

        child_count = node.get_child_count()
        for i in range(child_count):
            if len(out) >= max_items:
                break
            child = node.get_child_at_index(i)
            if child:
                _walk_tree(child, out, depth + 1, max_items, max_depth)
    except Exception:
        pass


def _get_active_window_node():
    """Locates the currently active window node via AT-SPI."""
    Atspi = _get_atspi()
    if not Atspi:
        return None

    try:
        desk = Atspi.get_desktop(0)
        app_count = desk.get_child_count()
        
        # Priority 1: Window explicitly reporting ACTIVE state
        for i in range(app_count):
            app = desk.get_child_at_index(i)
            if not app:
                continue
            win_count = app.get_child_count()
            for j in range(win_count):
                w = app.get_child_at_index(j)
                if not w:
                    continue
                try:
                    state_set = w.get_state_set()
                    if state_set and state_set.contains(Atspi.StateType.ACTIVE):
                        return w
                except Exception:
                    pass

        # Priority 2: Fallback to top-level window reporting SHOWING / VISIBLE
        for i in range(app_count):
            app = desk.get_child_at_index(i)
            if not app or app.get_name() in ("ksmserver", "xembedsniproxy", "gmenudbusmenuproxy"):
                continue
            win_count = app.get_child_count()
            for j in range(win_count):
                w = app.get_child_at_index(j)
                if not w:
                    continue
                role = w.get_role_name()
                if role in ("frame", "dialog") and (w.get_name() or "").strip() not in ("", "Offscreen"):
                    return w

    except Exception as e:
        logger.warning(f"Error finding active window: {e}")

    return None


def _find_element(node, target_name: str, depth: int = 0, max_depth: int = 15):
    """Searches node hierarchy for element by name or action interface."""
    if depth > max_depth or node is None:
        return None

    try:
        node_name = (node.get_name() or "").strip().lower()
        query = target_name.strip().lower()

        # Check exact or substring match with action interface
        has_action = node.get_action_iface() is not None
        if (node_name == query or query in node_name) and has_action:
            return node

        count = node.get_child_count()
        for i in range(count):
            child = node.get_child_at_index(i)
            found = _find_element(child, target_name, depth + 1, max_depth)
            if found:
                return found
    except Exception:
        pass

    return None


def desktop_look(mode: str = "windows", max_chars: int = 4000) -> dict:
    """
    Inspect the desktop screen and GUI hierarchy.

    Args:
        mode:
          - 'windows': Fast overview of active and open windows (via kdotool or AT-SPI).
          - 'ui': Traverses the active window's accessibility widget tree (buttons, inputs, labels).
          - 'ocr': Fullscreen OCR capture using spectacle + tesseract (for non-accessible apps).
        max_chars: Maximum output characters to prevent context bloat.
    """
    mode = (mode or "windows").strip().lower()

    if mode == "windows":
        # Check kdotool
        if shutil.which("kdotool"):
            cmd = "echo 'ACTIVE WINDOW:'; kdotool getactivewindow getwindowname 2>/dev/null; echo '\nOPEN WINDOWS:'; kdotool search --name '' getwindowname %@ 2>/dev/null"
            res = _sh(cmd)
            if res.strip():
                return {"success": True, "output": res[:max_chars]}

        # AT-SPI fallback for window enumeration
        Atspi = _get_atspi()
        if Atspi:
            try:
                desk = Atspi.get_desktop(0)
                windows = []
                for i in range(desk.get_child_count()):
                    app = desk.get_child_at_index(i)
                    if not app:
                        continue
                    app_name = app.get_name() or "Unknown"
                    for j in range(app.get_child_count()):
                        w = app.get_child_at_index(j)
                        if not w:
                            continue
                        role = w.get_role_name()
                        w_name = w.get_name() or ""
                        if role in ("frame", "dialog") and w_name and w_name != "Offscreen":
                            active = " [ACTIVE]" if w.get_state_set().contains(Atspi.StateType.ACTIVE) else ""
                            windows.append(f"- [{app_name}] {w_name}{active}")

                if windows:
                    output = "ACTIVE & OPEN WINDOWS (via AT-SPI):\n" + "\n".join(windows)
                    return {"success": True, "output": output[:max_chars]}
            except Exception as e:
                logger.debug(f"AT-SPI window enumeration failed: {e}")

        return {
            "success": False,
            "error": "kdotool not installed and no active windows found via AT-SPI. Install kdotool via 'sudo pacman -S kdotool'.",
            "output": ""
        }

    elif mode == "ui":
        w = _get_active_window_node()
        if not w:
            return {
                "success": False,
                "error": "No active window exposed via AT-SPI. The app may be an Electron/Chromium app launched without accessibility (try mode='ocr').",
                "output": ""
            }

        out = []
        win_title = w.get_name() or "Active Window"
        out.append(f"=== {win_title} ({w.get_role_name()}) ===")
        _walk_tree(w, out)

        if len(out) <= 1:
            return {
                "success": False,
                "error": f"Active window '{win_title}' exposed no interactive child widgets. Try mode='ocr'.",
                "output": "\n".join(out)
            }

        return {"success": True, "output": "\n".join(out)[:max_chars]}

    elif mode == "ocr":
        if not shutil.which("spectacle"):
            return {"success": False, "error": "spectacle is not installed for taking screenshots."}
        if not shutil.which("tesseract"):
            return {"success": False, "error": "tesseract is not installed for OCR."}

        # Take silent background fullscreen screenshot
        _sh(f"spectacle -b -n -f -o {TMP_SNAP_PATH}")
        if not os.path.exists(TMP_SNAP_PATH):
            return {"success": False, "error": "Failed to capture screenshot with spectacle."}

        # Run OCR
        ocr_text = _sh(f"tesseract {TMP_SNAP_PATH} stdout 2>/dev/null")

        # Cleanup snap file
        try:
            os.remove(TMP_SNAP_PATH)
        except Exception:
            pass

        clean_ocr = (ocr_text or "").strip()
        if not clean_ocr:
            return {"success": True, "output": "(No readable text detected on screen via OCR)"}

        return {"success": True, "output": clean_ocr[:max_chars]}

    else:
        return {"success": False, "error": f"Unknown mode '{mode}'. Valid modes are: windows | ui | ocr"}


def desktop_click_element(name: str) -> dict:
    """
    Clicks an interactive UI element by its exact or partial name via the AT-SPI action interface.
    Fast and native — triggers the widget directly without moving the hardware mouse cursor.
    """
    if not name or not isinstance(name, str):
        return {"success": False, "error": "Element name is required."}

    w = _get_active_window_node()
    if not w:
        return {"success": False, "error": "No active window found. Use desktop_look(mode='windows') to check open apps."}

    node = _find_element(w, name)
    if not node:
        return {
            "success": False,
            "error": f"Element '{name}' not found in active window '{w.get_name()}'. Run desktop_look(mode='ui') to check available elements."
        }

    try:
        action_iface = node.get_action_iface()
        if action_iface:
            action_iface.do_action(0)
            return {"success": True, "output": f"Successfully clicked UI element '{name}' ({node.get_role_name()})."}
    except Exception as e:
        return {"success": False, "error": f"Failed to click element '{name}': {e}"}

    return {"success": False, "error": f"Element '{name}' has no clickable action interface."}


def desktop_click(x: int | float, y: int | float) -> dict:
    """
    Moves mouse and clicks screen coordinates (x, y) via ydotool.
    Use desktop_click_element() first; use this as a coordinate fallback.
    """
    if not shutil.which("ydotool"):
        return {"success": False, "error": "ydotool is not installed. Install via 'sudo pacman -S ydotool'."}

    res = _sh(f"ydotool mousemove -a -x {int(x)} -y {int(y)} && ydotool click 0xC0")
    if "failed to connect" in res.lower() or "socket" in res.lower():
        return {
            "success": False,
            "error": f"ydotool socket error: {res}. Ensure ydotoold service is running (systemctl --user start ydotoold)."
        }

    return {"success": True, "output": f"Clicked coordinates ({int(x)}, {int(y)})."}


def desktop_type(text: str) -> dict:
    """
    Types text into the currently focused input field via ydotool.
    Make sure the desired text box has focus before calling.
    """
    if not text:
        return {"success": False, "error": "Text is required to type."}

    if not shutil.which("ydotool"):
        return {"success": False, "error": "ydotool is not installed. Install via 'sudo pacman -S ydotool'."}

    quoted = subprocess.list2cmdline([text])
    res = _sh(f"ydotool type -- {quoted}")
    if "failed to connect" in res.lower() or "socket" in res.lower():
        return {
            "success": False,
            "error": f"ydotool socket error: {res}. Ensure ydotoold service is running."
        }

    return {"success": True, "output": f"Typed text ({len(text)} chars) into active field."}


def desktop_press_keys(combo: str) -> dict:
    """
    Sends key combinations or special keystrokes via ydotool.
    Accepts names like 'enter', 'tab', 'esc', 'ctrl+c', 'ctrl+v', or raw evdev codes ('28:1 28:0').
    """
    if not combo or not isinstance(combo, str):
        return {"success": False, "error": "Key combo is required."}

    if not shutil.which("ydotool"):
        return {"success": False, "error": "ydotool is not installed. Install via 'sudo pacman -S ydotool'."}

    cleaned = combo.strip().lower()
    evdev_code = KEYMAP.get(cleaned, combo)

    res = _sh(f"ydotool key {evdev_code}")
    if "failed to connect" in res.lower() or "socket" in res.lower():
        return {
            "success": False,
            "error": f"ydotool socket error: {res}. Ensure ydotoold service is running."
        }

    return {"success": True, "output": f"Pressed key combo: '{combo}'."}
