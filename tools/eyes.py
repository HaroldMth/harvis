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

        # HARVIS_GEO_PATCH: focused window by PID (KWin) before trusting AT-SPI ACTIVE state
        _pid = _sh("kdotool getactivewindow getwindowpid 2>/dev/null").strip()
        if _pid.isdigit():
            for i in range(app_count):
                app = desk.get_child_at_index(i)
                try:
                    if app and app.get_process_id() == int(_pid):
                        for j in range(app.get_child_count()):
                            w = app.get_child_at_index(j)
                            if w and w.get_role_name() in ("frame", "dialog"):
                                return w
                except Exception:
                    pass
        
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


WCU_BIN = os.path.expanduser("~/HARVIS/.venv/bin/wayland-computer")
if not os.path.exists(WCU_BIN):
    WCU_BIN = shutil.which("wayland-computer") or "wayland-computer"


def _wcu(*args, timeout: int = 15) -> tuple[int, str]:
    """Execute wayland-computer command using instant input style."""
    env = {
        **os.environ,
        "WCU_INPUT_STYLE": "instant",
        "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY", "wayland-0"),
        "DISPLAY": os.environ.get("DISPLAY", ":0"),
        "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"),
        "DBUS_SESSION_BUS_ADDRESS": os.environ.get("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus"),
    }
    cmd = [WCU_BIN, *(str(a) for a in args)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        out = (r.stdout + r.stderr).strip()
        return r.returncode, out
    except subprocess.TimeoutExpired:
        return 124, f"wayland-computer timed out after {timeout}s"
    except Exception as e:
        return 1, f"wayland-computer failed: {e}"


def desktop_click_element(name: str) -> dict:
    """
    Clicks an interactive UI element by name in the active window.
    First searches AT-SPI accessibility tree to obtain physical screen bounds.
    If physical bounds exist, clicks its center natively via wayland-computer.
    If not found in UI tree, falls back to OCR text matching (desktop_click_text).
    """
    if not name or not isinstance(name, str):
        return {"success": False, "error": "Element name is required."}

    # 1. Try AT-SPI accessibility element with physical bounds
    w = _get_active_window_node()
    if w:
        node = _find_element(w, name)
        if node:
            try:
                comp = node.get_component_iface()
                if comp:
                    import gi
                    gi.require_version("Atspi", "2.0")
                    from gi.repository import Atspi
                    ext = comp.get_extents(Atspi.CoordType.SCREEN)
                    if ext and ext.width > 0 and ext.height > 0:
                        cx = ext.x + ext.width // 2
                        cy = ext.y + ext.height // 2
                        r = desktop_click(cx, cy)
                        if r.get("success"):
                            r["output"] = f"Clicked '{name}' ({node.get_role_name()}) at physical screen position ({cx}, {cy})."
                            return r
            except Exception as e:
                logger.debug(f"AT-SPI bounds check failed for {name}: {e}")

    # 2. Fall back to OCR text matching
    ocr_res = desktop_click_text(name)
    if ocr_res.get("success"):
        return ocr_res

    # 3. Last fallback: try AT-SPI synthetic action iface
    if w:
        node = _find_element(w, name)
        if node:
            try:
                action_iface = node.get_action_iface()
                if action_iface:
                    action_iface.do_action(0)
                    return {"success": True, "output": f"Triggered action on '{name}' ({node.get_role_name()}) via AT-SPI."}
            except Exception:
                pass

    return {
        "success": False,
        "error": f"Element '{name}' not found on screen via UI tree or OCR text match."
    }


# ===== HARVIS_GEO_PATCH_BEGIN: precision hands & eyes =====
import math
import time

from tools import geometry as geo


def desktop_screen_info(verify: bool = False) -> dict:
    """Exact screen geometry in logical px, current cursor position, pointer calibration."""
    try:
        s = geo.get_screen()
        lines = [s.describe()]
        pos = geo.mouse_position()
        lines.append(f"Cursor now: {pos}" if pos else "Cursor position unavailable (kdotool missing?)")
        return {"success": True, "output": "\n".join(lines)}
    except geo.GeometryError as e:
        return {"success": False, "error": str(e)}


def desktop_click(x, y, button: str = "left", clicks: int = 1) -> dict:
    """Exact click at logical screen coordinates (x, y) via native Wayland portal."""
    try:
        xi, yi = int(round(float(x))), int(round(float(y)))
    except (TypeError, ValueError):
        return {"success": False, "error": "x and y must be numbers."}

    button = (button or "left").strip().lower()
    btn_arg = "double" if clicks == 2 else button

    rc, out = _wcu("click", xi, yi, btn_arg)
    if rc != 0:
        return {"success": False, "error": f"Click failed: {out}"}
    return {"success": True, "output": f"Clicked ({xi}, {yi}) with {button} button."}


def desktop_move(x, y) -> dict:
    """Move cursor to logical screen coordinates (x, y) via native Wayland portal."""
    try:
        xi, yi = int(round(float(x))), int(round(float(y)))
    except (TypeError, ValueError):
        return {"success": False, "error": "x and y must be numbers."}
    rc, out = _wcu("move", xi, yi)
    if rc != 0:
        return {"success": False, "error": f"Move failed: {out}"}
    return {"success": True, "output": f"Moved cursor to ({xi}, {yi})."}


def desktop_type(text: str) -> dict:
    """Type text (including Unicode) into the focused window/field via native Wayland portal."""
    if not text:
        return {"success": False, "error": "Text is required to type."}
    rc, out = _wcu("type", text)
    if rc != 0:
        return {"success": False, "error": f"Type failed: {out}"}
    return {"success": True, "output": f"Typed text ({len(text)} chars) into active field."}


def desktop_press_keys(combo: str) -> dict:
    """Press a key combo: 'enter', 'Return', 'ctrl+alt+t', 'ctrl+shift+tab', 'f5', 'esc'."""
    if not combo or not isinstance(combo, str):
        return {"success": False, "error": "Key combo is required."}
    clean = combo.strip()
    synonyms = {"enter": "Return", "esc": "Escape", "del": "Delete"}
    clean_mapped = synonyms.get(clean.lower(), clean)
    rc, out = _wcu("key", clean_mapped)
    if rc != 0:
        return {"success": False, "error": f"Key combo failed: {out}"}
    return {"success": True, "output": f"Pressed key combo: '{combo}'."}


def desktop_focus(window: str) -> dict:
    """Focus a window by substring of its title (e.g. 'Chrome', 'WhatsApp', 'Kate')."""
    if not window or not isinstance(window, str):
        return {"success": False, "error": "Window name substring is required."}
    rc, out = _wcu("focus", window.strip())
    if rc != 0:
        return {"success": False, "error": f"Could not focus window: {out}"}
    return {"success": True, "output": out or f"Focused window matching '{window}'."}


def desktop_scroll(dy: int) -> dict:
    """Scroll vertically (positive scrolls down, negative scrolls up)."""
    try:
        dy_int = int(dy)
    except (TypeError, ValueError):
        return {"success": False, "error": "dy must be an integer."}
    rc, out = _wcu("scroll", dy_int)
    if rc != 0:
        return {"success": False, "error": f"Scroll failed: {out}"}
    return {"success": True, "output": f"Scrolled by {dy_int} units."}


# ---- OCR with real bounding boxes, in logical coordinates ----------------------
def _wait_stable(path: str, timeout: float = 5.0) -> bool:
    end = time.time() + timeout
    last = -1
    while time.time() < end:
        if os.path.exists(path):
            size = os.path.getsize(path)
            if size > 0 and size == last:
                return True
            last = size
        time.sleep(0.15)
    return False


def _capture(path: str = TMP_SNAP_PATH):
    """Screenshot with geometry sampled before AND after, so the pixel->logical map is trustworthy."""
    for _ in range(3):
        s1 = geo.get_screen()
        if os.path.exists(path):
            os.remove(path)
        if shutil.which("spectacle"):
            geo.run(["spectacle", "-b", "-n", "-f", "-o", path], 20)
        elif shutil.which("grim"):
            geo.run(["grim", path], 20)
        else:
            raise geo.GeometryError("No screenshot tool (need spectacle or grim).")
        if not _wait_stable(path):
            continue
        if geo.get_screen().key != s1.key:
            continue
        w, h = geo.png_size(path)
        return path, s1, geo.build_image_map(s1, w, h)
    raise geo.GeometryError("Could not take a consistent screenshot (screen layout kept changing or capture failed).")


def _parse_tsv(tsv: str, factor: float, imap, dy: float = 0.0):
    """dy = vertical offset (in OCR-image px) of this strip inside the full image."""
    words = []
    for line in tsv.splitlines()[1:]:
        p = line.split("\t")
        if len(p) < 12 or p[0] != "5":
            continue
        try:
            conf = float(p[10])
            l, t, w, h = (int(v) for v in p[6:10])
        except ValueError:
            continue
        l, t, w, h = l / factor, (t + dy) / factor, w / factor, h / factor
        text = p[11].strip()
        norm = re.sub(r"\W+", "", text.lower())
        if not norm or conf < 40:
            continue
        x0, y0 = imap.to_logical(l, t)
        x1, y1 = imap.to_logical(l + w, t + h)
        words.append({"text": text, "norm": norm, "conf": conf, "x0": x0, "y0": y0, "x1": x1, "y1": y1})
    return words


def _tess_tsv(src: str, threads: int = 2) -> str:
    r = subprocess.run(["tesseract", src, "stdout", "--psm", "11", "--dpi", "300", "tsv"],
                       capture_output=True, text=True, timeout=180,
                       env={**os.environ, "OMP_THREAD_LIMIT": str(max(1, threads)), "OMP_WAIT_POLICY": "PASSIVE"})
    return r.stdout


def _merge_words(base, extra):
    for w in extra:
        cx, cy = (w["x0"] + w["x1"]) / 2, (w["y0"] + w["y1"]) / 2
        h = max(w["y1"] - w["y0"], 1.0)
        dup = any(b["norm"] == w["norm"] and abs((b["x0"] + b["x1"]) / 2 - cx) <= h
                  and abs((b["y0"] + b["y1"]) / 2 - cy) <= 0.5 * h for b in base)
        if not dup:
            base.append(w)
    return base


def _ocr_words(path: str, imap):
    """
    Word boxes in logical px. With Pillow: autocontrast, 2x upscale, normal AND inverted polarity,
    each cut into overlapping horizontal strips; every strip is a separate single-thread tesseract
    process, run in parallel across the CPU cores.
    """
    try:
        from PIL import Image, ImageOps
    except Exception:  # noqa: BLE001
        return _parse_tsv(_tess_tsv(path), 1, imap)
    from concurrent.futures import ThreadPoolExecutor
    factor = 2 if os.environ.get("HARVIS_OCR_UPSCALE", "1") != "0" else 1
    cores = os.cpu_count() or 2
    strips = max(1, min(6, cores // 2))
    jobs, tmps = [], []
    try:
        with Image.open(path) as im:
            gray = ImageOps.autocontrast(im.convert("L"))
            if factor != 1:
                gray = gray.resize((gray.width * factor, gray.height * factor), Image.LANCZOS)
            W, H = gray.size
            overlap = 60 * factor
            for invert in (False, True):
                img = ImageOps.invert(gray) if invert else gray
                for k in range(strips):
                    y0 = max(0, k * H // strips - overlap)
                    y1 = min(H, (k + 1) * H // strips + overlap)
                    tmp = f"{path}.p{int(invert)}s{k}.png"
                    img.crop((0, y0, W, y1)).save(tmp)
                    tmps.append(tmp)
                    jobs.append((tmp, y0))
        with ThreadPoolExecutor(max_workers=max(1, min(len(jobs), cores))) as pool:
            results = list(pool.map(lambda j: _tess_tsv(j[0], 1), jobs))
        words = []
        for (tmp, y0), tsv in zip(jobs, results):  # fixed order => deterministic merge
            _merge_words(words, _parse_tsv(tsv, factor, imap, dy=y0))
    finally:
        for t in tmps:
            if os.path.exists(t):
                os.remove(t)
    return words


def _chain_matches(words, target):
    """Find phrases geometrically: each next word must be on the same line, just to the right."""
    n = len(target)
    out = []
    for w0 in words:
        if w0["norm"] != target[0]:
            continue
        seg = [w0]
        ok = True
        for k in range(1, n):
            cand = [w for w in words if w["norm"] == target[k] and _adjacent(seg[-1], w)]
            if not cand:
                ok = False
                break
            seg.append(min(cand, key=lambda w: w["x0"] - seg[-1]["x1"]))
        if not ok:
            continue
        x0 = min(w["x0"] for w in seg); y0 = min(w["y0"] for w in seg)
        x1 = max(w["x1"] for w in seg); y1 = max(w["y1"] for w in seg)
        m = {"cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2, "box": (x0, y0, x1, y1),
             "conf": min(w["conf"] for w in seg)}
        if any(abs(m["cx"] - o["cx"]) < 3 and abs(m["cy"] - o["cy"]) < 3 for o in out):
            continue
        out.append(m)
    return out


def _adjacent(a, b) -> bool:
    ha, hb = a["y1"] - a["y0"], b["y1"] - b["y0"]
    hmax = max(ha, hb, 1.0)
    same_line = abs((a["y0"] + a["y1"]) / 2 - (b["y0"] + b["y1"]) / 2) <= 0.5 * hmax
    gap = b["x0"] - a["x1"]
    return same_line and -2.0 <= gap <= 1.2 * hmax


def _find_text(text: str):
    if shutil.which("tesseract") is None:
        raise geo.GeometryError("tesseract is not installed (sudo pacman -S tesseract tesseract-data-eng).")
    path, screen, imap = _capture()
    try:
        words = _ocr_words(path, imap)
    finally:
        if os.path.exists(path):
            os.remove(path)
    target = [re.sub(r"\W+", "", t.lower()) for t in text.split()]
    target = [t for t in target if t]
    if not target:
        raise geo.GeometryError("Search text has no letters or digits.")
    matches = _chain_matches(words, target)
    matches.sort(key=lambda m: (m["cy"], m["cx"]))
    return matches, screen


def _fmt_matches(matches) -> str:
    return "\n".join(
        f"#{i} center=({geo.round_half_up(m['cx'])},{geo.round_half_up(m['cy'])}) "
        f"box=[{m['box'][0]:.0f},{m['box'][1]:.0f},{m['box'][2]:.0f},{m['box'][3]:.0f}] conf={m['conf']:.0f}"
        for i, m in enumerate(matches, 1))


def desktop_locate_text(text: str) -> dict:
    """OCR the whole screen and list every exact match of `text` with its center in logical screen coordinates."""
    if not text or not isinstance(text, str):
        return {"success": False, "error": "text is required."}
    try:
        matches, screen = _find_text(text)
    except geo.GeometryError as e:
        return {"success": False, "error": str(e)}
    if not matches:
        return {"success": False, "error": f"'{text}' not found on screen via OCR."}
    return {"success": True, "output": f"{len(matches)} match(es) for '{text}':\n{_fmt_matches(matches)}"}


def desktop_click_text(text: str, nth: int = None, button: str = "left", clicks: int = 1) -> dict:
    """
    Find `text` on screen via OCR and click the exact center of its bounding box.
    If it appears more than once, nth (1-based, top-to-bottom then left-to-right) is required;
    the error lists every candidate so the caller can choose.
    """
    if not text or not isinstance(text, str):
        return {"success": False, "error": "text is required."}
    try:
        matches, screen = _find_text(text)
    except geo.GeometryError as e:
        return {"success": False, "error": str(e)}
    if not matches:
        return {"success": False, "error": f"'{text}' not found on screen via OCR."}
    if nth is None:
        if len(matches) > 1:
            return {"success": False,
                    "error": f"'{text}' matches {len(matches)} places; pass nth.\n{_fmt_matches(matches)}"}
        nth = 1
    try:
        nth = int(nth)
    except (TypeError, ValueError):
        return {"success": False, "error": "nth must be an integer."}
    if not 1 <= nth <= len(matches):
        return {"success": False, "error": f"nth={nth} out of range; {len(matches)} match(es).\n{_fmt_matches(matches)}"}
    m = matches[nth - 1]
    r = desktop_click(m["cx"], m["cy"], button=button, clicks=clicks)
    if r.get("success"):
        r["output"] += f" (OCR match {nth}/{len(matches)} for '{text}', conf {m['conf']:.0f})"
    return r


# Alias desktop_find to desktop_locate_text for clean agent discovery
desktop_find = desktop_locate_text
# ===== HARVIS_GEO_PATCH_END =====
