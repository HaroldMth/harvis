"""
HARVIS Aim — Unified visual click with coarse-to-fine grounding and verification.

Implements the pipeline recommended for accurate vision-LLM agents:
  1. Coarse ground  — locate the general area on the full screenshot
  2. Fine ground    — zoom into that area, find the exact target
  3. Confirm pass   — "is the red box on {description}?" yes/no
  4. Click          — native Wayland portal
  5. Settle         — wait for animations / popups to finish
  6. Pixel diff     — did anything visibly change?
  7. LLM judge      — did the result match the expected outcome?

Returns a structured result dict so the calling model cannot claim success
unless met_expectation is True.

Single public tool: desktop_aim(description, expect, scope)
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("harvis.aim")

# ---------------------------------------------------------------------------
# Region  — always stores REAL screen pixels; boxes remap relative to self
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Region:
    x: int
    y: int
    w: int
    h: int  # all in real screen (logical) pixels

    # Convert a Gemini normalized box [ymin,xmin,ymax,xmax] (0-1000)
    # that was returned for an image of this region, back to a child Region
    # in real screen pixels.
    def child(self, box: list) -> "Region":
        ymin, xmin, ymax, xmax = box
        cx = self.x + round(xmin * self.w / 1000)
        cy = self.y + round(ymin * self.h / 1000)
        cw = max(1, round((xmax - xmin) * self.w / 1000))
        ch = max(1, round((ymax - ymin) * self.h / 1000))
        return Region(cx, cy, cw, ch)

    # Pad outward by frac (e.g. 0.25 = 25% each side), clamped to bounds.
    def pad(self, frac: float, bounds: "Region",
            min_w: int = 160, min_h: int = 80) -> "Region":
        w = max(self.w * (1 + 2 * frac), min_w)
        h = max(self.h * (1 + 2 * frac), min_h)
        cx = self.x + self.w / 2
        cy = self.y + self.h / 2
        x0 = max(bounds.x, int(cx - w / 2))
        y0 = max(bounds.y, int(cy - h / 2))
        x1 = min(bounds.x + bounds.w, int(cx + w / 2))
        y1 = min(bounds.y + bounds.h, int(cy + h / 2))
        return Region(x0, y0, max(1, x1 - x0), max(1, y1 - y0))

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2

    def crop(self, img) -> "PIL.Image.Image":
        return img.crop((self.x, self.y, self.x + self.w, self.y + self.h))

    def valid(self) -> bool:
        return self.w > 0 and self.h > 0

    def __repr__(self) -> str:
        return f"Region(x={self.x}, y={self.y}, w={self.w}, h={self.h})"


# ---------------------------------------------------------------------------
# Gemini Vision helpers
# ---------------------------------------------------------------------------

def _b64(img) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _gemini(prompt: str, img, response_mime: str = "application/json",
            timeout: int = 20) -> str:
    """POST one image + prompt to Gemini Flash Lite. Returns raw response text."""
    import requests
    from config import GEMINI_API_KEY

    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/png", "data": _b64(img)}},
            ]
        }],
        "generationConfig": {"response_mime_type": response_mime},
    }

    models = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.5-flash"]
    last_err = None
    for model in models:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={GEMINI_API_KEY}"
        )
        try:
            import requests as _req
            r = _req.post(url, json=payload, timeout=timeout)
            if r.status_code == 200:
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = str(e)
    raise RuntimeError(f"Gemini call failed: {last_err}")


def ask_box(img, description: str, allow_null: bool = True) -> Optional[list]:
    """
    Ask Gemini to find `description` in `img`.
    Returns [ymin, xmin, ymax, xmax] (0-1000) or None if not found.
    """
    null_clause = (
        'If the element is not visible, return {"box_2d": null}.'
        if allow_null else ""
    )
    prompt = (
        f"You are a GUI element locator. "
        f"Find: \"{description}\".\n"
        f"Return ONLY valid JSON: {{\"box_2d\": [ymin, xmin, ymax, xmax]}} "
        f"where all values are integers 0-1000 relative to this image. "
        f"{null_clause}"
    )
    try:
        raw = json.loads(_gemini(prompt, img))
        box = raw.get("box_2d") if isinstance(raw, dict) else raw
        if box is None:
            return None
        if not (isinstance(box, list) and len(box) == 4):
            return None
        return [float(v) for v in box]
    except Exception as e:
        logger.warning(f"ask_box failed: {e}")
        return None


def ask_yesno(img, question: str) -> bool:
    """Ask Gemini a yes/no question about an image."""
    prompt = (
        f"{question}\n"
        f"Return ONLY valid JSON: {{\"answer\": \"yes\"}} or {{\"answer\": \"no\"}}."
    )
    try:
        raw = json.loads(_gemini(prompt, img))
        return str(raw.get("answer", "no")).lower().strip() == "yes"
    except Exception as e:
        logger.warning(f"ask_yesno failed: {e}")
        return False


def ask_judge(before_img, after_img, expect: str) -> str:
    """
    Given before/after screenshots, ask Gemini whether `expect` was achieved.
    Returns "yes", "no", or "unsure".
    """
    prompt = (
        f"You are a verification judge. "
        f"Compare the BEFORE image (left side of context) and AFTER image.\n"
        f"Did this happen: \"{expect}\"?\n"
        f"Return ONLY valid JSON: {{\"result\": \"yes\"}}, {{\"result\": \"no\"}}, "
        f"or {{\"result\": \"unsure\"}}."
    )
    # Compose side-by-side for comparison
    try:
        from PIL import Image
        bw, bh = before_img.size
        aw, ah = after_img.size
        combined_w = bw + aw
        combined_h = max(bh, ah)
        combined = Image.new("RGB", (combined_w, combined_h))
        combined.paste(before_img, (0, 0))
        combined.paste(after_img, (bw, 0))
        raw = json.loads(_gemini(prompt, combined))
        return str(raw.get("result", "unsure")).lower().strip()
    except Exception as e:
        logger.warning(f"ask_judge failed: {e}")
        return "unsure"


# ---------------------------------------------------------------------------
# Screen settle + pixel diff
# ---------------------------------------------------------------------------

def _fingerprint(img):
    """Cheap perceptual fingerprint for fast pixel diff."""
    try:
        import numpy as np
        return np.asarray(
            img.convert("L").resize((128, 72)), dtype=np.float32
        ) / 255.0
    except ImportError:
        # numpy unavailable: fall back to raw bytes hash
        buf = io.BytesIO()
        img.convert("L").resize((128, 72)).save(buf, format="PNG")
        return buf.getvalue()


def _diff(a, b) -> float:
    """Return mean absolute pixel difference (0.0–1.0)."""
    try:
        import numpy as np
        fa, fb = _fingerprint(a), _fingerprint(b)
        return float(np.abs(fa - fb).mean())
    except Exception:
        # Bytes fallback: 0 if identical, 1 if different
        return 0.0 if _fingerprint(a) == _fingerprint(b) else 1.0


def _grab_screen() -> "PIL.Image.Image":
    """Take a fresh screenshot, return PIL Image."""
    import subprocess, shutil
    from PIL import Image
    tmp = "/tmp/harvis_aim_snap.png"
    if os.path.exists(tmp):
        os.remove(tmp)
    if shutil.which("spectacle"):
        subprocess.run(["spectacle", "-b", "-n", "-f", "-o", tmp],
                       capture_output=True, timeout=10)
    elif shutil.which("grim"):
        subprocess.run(["grim", tmp], capture_output=True, timeout=10)
    else:
        raise RuntimeError("No screenshot tool (need spectacle or grim)")
    return Image.open(tmp).copy()


def settle(timeout: float = 1.5, interval: float = 0.15) -> "PIL.Image.Image":
    """Wait until the screen stops changing, return stable screenshot."""
    prev = _grab_screen()
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        time.sleep(interval)
        cur = _grab_screen()
        if _diff(prev, cur) < 0.002:
            return cur
        prev = cur
    return prev


# ---------------------------------------------------------------------------
# Draw a red box on a crop for the confirm pass
# ---------------------------------------------------------------------------

def _draw_box(img, target: Region, ctx: Region):
    """Return a copy of img with a red rectangle marking target."""
    try:
        from PIL import ImageDraw
        out = img.copy().convert("RGB")
        draw = ImageDraw.Draw(out)
        rx = target.x - ctx.x
        ry = target.y - ctx.y
        draw.rectangle(
            [rx, ry, rx + target.w, ry + target.h],
            outline=(255, 0, 0), width=3
        )
        return out
    except Exception:
        return img


# ---------------------------------------------------------------------------
# The unified public tool
# ---------------------------------------------------------------------------

def desktop_aim(description: str, expect: str = None, scope: str = "screen") -> dict:
    """
    Click any element on the screen by natural-language description.

    This is the ONLY click tool you should use for graphical elements, icons,
    buttons, avatars, menu items, dropdowns, and anything without reliable
    readable text.

    Pipeline (fully automatic):
      1. Coarse ground on full screenshot → locates the region
      2. Fine ground on zoomed crop → finds exact target
      3. Confirm pass → verifies the box is on the right thing
      4. Click via native Wayland portal
      5. Settle → waits for animations to finish
      6. Pixel diff → checks if anything changed
      7. LLM judge → checks if `expect` was met (if provided)

    Args:
        description: What to click. Be specific. Include location context.
                     e.g. "circular profile avatar in the Chrome top toolbar, far right"
                     e.g. "second Mibe row in the Chrome profile dropdown list"
        expect:      What you expect to happen after clicking.
                     e.g. "a profile dropdown menu opens below the toolbar"
                     If omitted, only pixel diff is used for verification.
        scope:       "screen" (default), "window" (active window bounds), or
                     "WxH+X+Y" pixel rect to restrict search area.

    Returns:
        {success, clicked_at, changed, met_expectation, output/error}
    """
    try:
        from PIL import Image
    except ImportError:
        return {"success": False, "error": "Pillow not installed (pip install Pillow)"}

    # -- Resolve scope --------------------------------------------------------
    screen_w, screen_h = 1920, 1080  # fallback
    try:
        import tools.geometry as geo
        s = geo.get_screen()
        screen_w, screen_h = int(s.width), int(s.height)
    except Exception:
        pass

    SCREEN = Region(0, 0, screen_w, screen_h)

    if scope == "screen":
        search_region = SCREEN
    elif scope == "window":
        try:
            import subprocess
            out = subprocess.run(
                ["kdotool", "getactivewindow", "getwindowgeometry"],
                capture_output=True, text=True, timeout=5
            ).stdout
            # parse "Position: X,Y\nGeometry: WxH"
            import re
            pos = re.search(r"Position:\s*(\d+),(\d+)", out)
            geo_m = re.search(r"Geometry:\s*(\d+)x(\d+)", out)
            if pos and geo_m:
                search_region = Region(
                    int(pos.group(1)), int(pos.group(2)),
                    int(geo_m.group(1)), int(geo_m.group(2))
                )
            else:
                search_region = SCREEN
        except Exception:
            search_region = SCREEN
    else:
        # WxH+X+Y
        import re
        m = re.match(r"(\d+)x(\d+)\+(\d+)\+(\d+)", scope)
        if m:
            search_region = Region(int(m.group(3)), int(m.group(4)),
                                   int(m.group(1)), int(m.group(2)))
        else:
            search_region = SCREEN

    # -- 1. Take screenshot ---------------------------------------------------
    try:
        shot = _grab_screen()
    except Exception as e:
        return {"success": False, "error": f"Screenshot failed: {e}"}

    # Crop to scope
    scope_img = search_region.crop(shot) if search_region != SCREEN else shot

    # -- 2. Coarse ground: find the general area ------------------------------
    coarse_box = ask_box(
        scope_img,
        f"the UI area or container that holds: {description}",
        allow_null=True
    )

    if coarse_box is None:
        # Fallback: treat the whole scope as the coarse region
        coarse_region = search_region
        logger.info("Coarse ground found nothing — using full scope")
    else:
        coarse_region = search_region.child(coarse_box)
        # Pad 25% each side so we don't clip the target
        coarse_region = coarse_region.pad(0.25, SCREEN, min_w=120, min_h=60)

    logger.info(f"Coarse region: {coarse_region}")

    # -- 3. Fine ground: zoom into coarse region, find exact target -----------
    fine_img = coarse_region.crop(shot)
    fine_box = ask_box(fine_img, description, allow_null=True)

    if fine_box is None:
        # Try once more on full scope without coarse step
        fine_box = ask_box(scope_img, description, allow_null=True)
        if fine_box is None:
            return {
                "success": False,
                "error": f"Element not found on screen: '{description}'"
            }
        target_region = search_region.child(fine_box)
    else:
        target_region = coarse_region.child(fine_box)

    logger.info(f"Fine target: {target_region}, center={target_region.center}")

    # -- 4. Confirm pass: draw red box, ask yes/no ----------------------------
    ctx = target_region.pad(2.0, SCREEN, min_w=240, min_h=120)
    confirm_img = _draw_box(ctx.crop(shot), target_region, ctx)
    confirmed = ask_yesno(
        confirm_img,
        f"Is the red rectangle correctly placed on: {description}? "
        f"Answer yes only if the red box is clearly on the right element."
    )

    if not confirmed:
        # One retry with a tighter description on the full scope
        logger.info("Confirm failed — retrying fine ground on full scope")
        retry_box = ask_box(scope_img, description, allow_null=True)
        if retry_box is None:
            return {
                "success": False,
                "error": (
                    f"Could not confirm target '{description}' on screen. "
                    f"The element may not be visible — take a screenshot first to verify."
                )
            }
        target_region = search_region.child(retry_box)
        ctx = target_region.pad(2.0, SCREEN, min_w=240, min_h=120)
        confirm_img = _draw_box(ctx.crop(shot), target_region, ctx)
        confirmed = ask_yesno(
            confirm_img,
            f"Is the red rectangle correctly placed on: {description}?"
        )
        if not confirmed:
            return {
                "success": False,
                "error": (
                    f"Cannot confidently locate '{description}'. "
                    f"The element may be obscured or not on screen."
                )
            }

    cx, cy = target_region.center

    # -- 5. Click -------------------------------------------------------------
    try:
        from tools.eyes import _wcu
        rc, out = _wcu("click", cx, cy, "left")
        if rc != 0:
            return {"success": False, "error": f"Click failed: {out}"}
    except Exception as e:
        return {"success": False, "error": f"Click error: {e}"}

    # -- 6. Settle + pixel diff -----------------------------------------------
    before_crop = ctx.crop(shot)  # context area before click
    after_shot = settle(timeout=1.5, interval=0.15)
    after_crop = ctx.crop(after_shot)

    global_diff = _diff(shot, after_shot)
    local_diff = _diff(before_crop, after_crop)
    changed = max(global_diff, local_diff) > 0.004

    # -- 7. LLM judge (only if something changed and expect is set) -----------
    met_expectation = None
    if expect and changed:
        judge_result = ask_judge(before_crop, after_crop, expect)
        met_expectation = judge_result  # "yes" / "no" / "unsure"
    elif expect and not changed:
        met_expectation = "no"  # nothing changed, expectation not met

    # -- Build result ---------------------------------------------------------
    status_parts = [f"Aimed at '{description}' and clicked ({cx}, {cy})."]
    if changed:
        status_parts.append("Screen changed after click ✓")
    else:
        status_parts.append(
            "WARNING: No visible change detected after click — "
            "the click may have missed or the target was already in that state."
        )
    if met_expectation:
        status_parts.append(f"Expectation '{expect}': {met_expectation}.")

    return {
        "success": True,
        "clicked_at": [cx, cy],
        "changed": changed,
        "met_expectation": met_expectation,
        "output": " ".join(status_parts),
    }
