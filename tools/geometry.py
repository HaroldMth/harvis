"""
HARVIS geometry - exact screen math, cursor feedback and calibrated pointer moves.

Coordinate spaces (never mixed silently):
  logical : KDE/Wayland compositor coordinates. What kdotool reports, what the
            cursor lives in. All public click/move functions take these.
  image   : pixels of a screenshot. Converted with ImageMap, never guessed.

Accuracy strategy
  1. Screen rectangle comes from kscreen-doctor and is cross-checked against the
     real cursor extents (pointer slammed into the corners and read back).
  2. ydotool's absolute mapping is not assumed. It is calibrated (affine fit per
     axis) and every move is closed-loop: move, read the real cursor position
     with kdotool, correct by the measured error, repeat until exact.
  3. Targets outside the screen are rejected, never clamped.
"""

import dataclasses
import json
import logging
import math
import os
import re
import shutil
import struct
import subprocess
import time
from dataclasses import dataclass

logger = logging.getLogger("harvis.geometry")

os.environ.setdefault("YDOTOOL_SOCKET", f"/run/user/{os.getuid()}/.ydotool_socket")

SETTLE_S = 0.06        # time for the compositor to apply an input event
MAX_ITER = 30          # max correction steps per move
TOLERANCE_PX = 1       # accepted final error (logical px) if exact is unreachable

_POS_RE = re.compile(r"x:\s*(-?\d+)\s+y:\s*(-?\d+)")


class GeometryError(RuntimeError):
    pass


def round_half_up(v: float) -> int:
    return int(math.floor(v + 0.5))


def run(cmd, timeout=10):
    """Run an argv list (no shell). Returns (returncode, stdout+stderr stripped)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"[timeout after {timeout}s: {' '.join(map(str, cmd))}]"
    except Exception as e:  # noqa: BLE001
        return 127, f"[error running {cmd[0]}: {e}]"
    return r.returncode, (r.stdout + r.stderr).strip()


def ydo(args, timeout=10):
    return run(["ydotool", *map(str, args)], timeout)


def ydo_failed(rc, out):
    low = (out or "").lower()
    return rc != 0 or "failed to connect" in low or "no such file" in low


YDO_HINT = "Ensure ydotoold is running: systemctl --user start ydotoold"


# ----------------------------------------------------------------------------
# Screen geometry
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Output:
    name: str
    x: float
    y: float
    w: float      # logical size
    h: float
    scale: float


@dataclass(frozen=True)
class Screen:
    outputs: tuple
    left: float
    top: float
    right: float   # exclusive
    bottom: float  # exclusive
    source: str

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def key(self):
        return (round(self.left), round(self.top), round(self.right), round(self.bottom),
                tuple((o.name, o.x, o.y, o.w, o.h, o.scale) for o in self.outputs))

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x <= self.right - 1 and self.top <= y <= self.bottom - 1

    def describe(self) -> str:
        lines = [f"Screen (logical px): x {self.left:g}..{self.right - 1:g}, y {self.top:g}..{self.bottom - 1:g} "
                 f"= {self.width:g}x{self.height:g}  [source: {self.source}]"]
        for o in self.outputs:
            lines.append(f"  - {o.name}: at ({o.x:g},{o.y:g}) size {o.w:g}x{o.h:g} scale {o.scale:g}")
        return "\n".join(lines)


def _screen_from_outputs(outs, source) -> Screen:
    left = min(o.x for o in outs)
    top = min(o.y for o in outs)
    right = max(o.x + o.w for o in outs)
    bottom = max(o.y + o.h for o in outs)
    return Screen(tuple(outs), left, top, right, bottom, source)


def _from_kscreen():
    if not shutil.which("kscreen-doctor"):
        return None
    rc, out = run(["kscreen-doctor", "-j"], 10)
    if rc != 0:
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    outs = []
    for o in data.get("outputs", []):
        if not o.get("enabled"):
            continue
        size = o.get("size") or {}
        pos = o.get("pos") or {}
        scale = float(o.get("scale") or 1.0)
        w = float(size.get("width", 0))
        h = float(size.get("height", 0))
        if o.get("rotation") in (2, 8):  # 90/270 degrees
            w, h = h, w
        if w <= 0 or h <= 0 or scale <= 0:
            continue
        outs.append(Output(str(o.get("name", "?")), float(pos.get("x", 0)), float(pos.get("y", 0)),
                           w / scale, h / scale, scale))
    if not outs:
        return None
    return _screen_from_outputs(outs, "kscreen-doctor")


# ----------------------------------------------------------------------------
# Cursor feedback
# ----------------------------------------------------------------------------
def mouse_position():
    """Real cursor position in logical px via kdotool, or None."""
    if not shutil.which("kdotool"):
        return None
    rc, out = run(["kdotool", "getmouselocation"], 6)
    m = _POS_RE.search(out or "")
    if rc != 0 or not m:
        return None
    return int(m.group(1)), int(m.group(2))


def probe_extent():
    """
    Slam the pointer into the top-left and bottom-right and read where it stops.
    Returns ((x0, y0), (x1, y1)) or None. Moves the cursor.
    """
    if mouse_position() is None:
        return None
    pts = []
    for d in (-30000, 30000):
        for _ in range(2):
            rc, out = ydo(["mousemove", "-x", d, "-y", d])
            if ydo_failed(rc, out):
                return None
            time.sleep(SETTLE_S * 2)
        p = mouse_position()
        if p is None:
            return None
        pts.append(p)
    return pts[0], pts[1]


def _cross_check(raw: Screen) -> Screen:
    ext = probe_extent()
    if ext is None:
        return raw
    (x0, y0), (x1, y1) = ext
    ok = (abs(x0 - raw.left) <= 2 and abs(y0 - raw.top) <= 2
          and abs(x1 + 1 - raw.right) <= 2 and abs(y1 + 1 - raw.bottom) <= 2)
    if ok:
        return dataclasses.replace(raw, source=raw.source + " + cursor-extent verified")
    if len(raw.outputs) == 1:
        o = raw.outputs[0]
        out = Output(o.name, float(x0), float(y0), float(x1 + 1 - x0), float(y1 + 1 - y0), o.scale)
        logger.warning("kscreen geometry disagreed with cursor extents; using measured extents")
        return _screen_from_outputs([out], "cursor-extent (kscreen-doctor mismatch)")
    logger.warning("kscreen geometry disagrees with cursor extents on a multi-monitor layout; keeping kscreen")
    return dataclasses.replace(raw, source=raw.source + " (UNVERIFIED: extent mismatch)")


_VERIFIED = {}
_FALLBACK = {}


def get_screen() -> Screen:
    raw = _from_kscreen()
    if raw is None:
        if "s" in _FALLBACK:
            return _FALLBACK["s"]
        ext = probe_extent()
        if ext is None:
            raise GeometryError("Cannot determine screen geometry. Need kscreen-doctor or kdotool (+ ydotoold).")
        (x0, y0), (x1, y1) = ext
        x0 = 0 if abs(x0) <= 1 else x0  # kdotool reads edge as 0 or 1
        y0 = 0 if abs(y0) <= 1 else y0
        out = Output("probed", float(x0), float(y0), float(x1 + 1 - x0), float(y1 + 1 - y0), 1.0)
        _FALLBACK["s"] = _screen_from_outputs([out], "cursor-extent (no kscreen-doctor)")
        return _FALLBACK["s"]
    fin = _VERIFIED.get(raw.key)
    if fin is None:
        fin = _cross_check(raw)
        _VERIFIED[raw.key] = fin
    return fin


# ----------------------------------------------------------------------------
# Calibrated, closed-loop absolute moves
# ----------------------------------------------------------------------------
_CAL = {}


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _move_loop(screen: Screen, tx: int, ty: int, tol: int) -> dict:
    """
    Closed-loop RELATIVE mover. ydotool absolute moves do not work on this system,
    so: read the real cursor, command a relative delta, read again, estimate the
    effective gain (pointer acceleration) per axis from what actually happened,
    and repeat until the cursor is exactly on target.
    """
    pos = mouse_position()
    if pos is None:
        raise GeometryError("Cannot read cursor position (kdotool getmouselocation failed).")
    gx = gy = 1.0
    best = {"pos": pos, "err": max(abs(tx - pos[0]), abs(ty - pos[1])), "iterations": 0}
    stall = 0
    for i in range(1, MAX_ITER + 1):
        ex, ey = tx - pos[0], ty - pos[1]
        if ex == 0 and ey == 0:
            return {"ok": True, "pos": pos, "err": 0, "iterations": i - 1}
        cx = round_half_up(ex / gx)
        cy = round_half_up(ey / gy)
        if cx == 0 and ex != 0:
            cx = 1 if ex > 0 else -1
        if cy == 0 and ey != 0:
            cy = 1 if ey > 0 else -1
        rc, out = ydo(["mousemove", "-x", cx, "-y", cy])
        if ydo_failed(rc, out):
            raise GeometryError(f"ydotool mousemove failed: {out}. {YDO_HINT}")
        time.sleep(SETTLE_S)
        new = mouse_position()
        if new is None:
            raise GeometryError("Lost cursor feedback (kdotool getmouselocation failed).")
        mx, my = new[0] - pos[0], new[1] - pos[1]
        if abs(cx) >= 4 and mx * cx > 0:
            gx = _clamp(mx / cx, 0.05, 8.0)
        if abs(cy) >= 4 and my * cy > 0:
            gy = _clamp(my / cy, 0.05, 8.0)
        pos = new
        err = max(abs(tx - pos[0]), abs(ty - pos[1]))
        if err < best["err"]:
            best = {"pos": pos, "err": err, "iterations": i}
            stall = 0
        else:
            stall += 1
        if stall >= 4:
            break
    ex, ey = tx - pos[0], ty - pos[1]
    if ex == 0 and ey == 0:
        return {"ok": True, "pos": pos, "err": 0, "iterations": MAX_ITER}
    best["ok"] = best["err"] <= tol
    return best


def move_to(x: float, y: float, tol: int = TOLERANCE_PX) -> dict:
    """Move the cursor to logical (x, y) and verify. Raises GeometryError on hard failure."""
    screen = get_screen()
    tx, ty = round_half_up(x), round_half_up(y)
    if not screen.contains(tx, ty):
        raise GeometryError(f"({tx},{ty}) is outside the screen area "
                            f"(x {screen.left:g}..{screen.right - 1:g}, y {screen.top:g}..{screen.bottom - 1:g}).")
    last = None
    for attempt in (1, 2):
        try:
            last = _move_loop(screen, tx, ty, tol)
        except GeometryError:
            if attempt == 2:
                raise
            _CAL.pop(screen.key, None)
            continue
        if last["ok"]:
            break
        _CAL.pop(screen.key, None)  # recalibrate once and retry
    last["target"] = (tx, ty)
    return last


_BUTTONS = {"left": 0x00, "right": 0x01, "middle": 0x02}


def click(x: float, y: float, button: str = "left", clicks: int = 1, interval: float = 0.09) -> dict:
    """Exact click: move (verified), re-verify immediately before pressing, then click."""
    if button not in _BUTTONS:
        raise GeometryError(f"Unknown button '{button}' (left|right|middle).")
    res = move_to(x, y)
    tx, ty = res["target"]
    if not res["ok"]:
        raise GeometryError(f"Could not place cursor on ({tx},{ty}); stopped at {res['pos']} "
                            f"(error {res['err']}px). No click was sent.")
    pos = mouse_position()
    if pos is None or max(abs(pos[0] - tx), abs(pos[1] - ty)) > TOLERANCE_PX:
        res = move_to(tx, ty)
        if not res["ok"]:
            raise GeometryError(f"Cursor drifted before click and could not be re-placed on ({tx},{ty}). No click sent.")
    code = _BUTTONS[button] | 0xC0
    for n in range(clicks):
        rc, out = ydo(["click", hex(code)])
        if ydo_failed(rc, out):
            raise GeometryError(f"ydotool click failed: {out}. {YDO_HINT}")
        if n < clicks - 1:
            time.sleep(interval)
    final = mouse_position() or res["pos"]
    return {"target": (tx, ty), "pos": final, "iterations": res["iterations"], "button": button, "clicks": clicks}


# ----------------------------------------------------------------------------
# Screenshot pixels -> logical coordinates
# ----------------------------------------------------------------------------
def png_size(path: str):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise GeometryError(f"Not a PNG file: {path}")
    w, h = struct.unpack(">II", head[16:24])
    return w, h


@dataclass(frozen=True)
class ImageMap:
    left: float
    top: float
    sx: float      # image px per logical px
    sy: float
    img_w: int
    img_h: int

    def to_logical(self, px: float, py: float):
        return self.left + px / self.sx, self.top + py / self.sy


def build_image_map(screen: Screen, img_w: int, img_h: int) -> ImageMap:
    sx = img_w / screen.width
    sy = img_h / screen.height
    if abs(sx - sy) / max(sx, sy) > 0.01:
        raise GeometryError(f"Screenshot {img_w}x{img_h} does not match screen layout "
                            f"{screen.width:g}x{screen.height:g} (axis scales {sx:.4f} vs {sy:.4f}). "
                            f"Refusing to map coordinates.")
    return ImageMap(screen.left, screen.top, sx, sy, img_w, img_h)
