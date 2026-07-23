import subprocess
import shutil
import os
import re
import glob
import urllib.parse
import urllib.request
import html
import json
import ipaddress
import logging
from datetime import datetime
from tools.browser import open_url

logger = logging.getLogger("harvis.tools")


def _run_cmd(cmd, timeout=5, input_text=None):
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        input=input_text,
    )


def _download(url: str, timeout: int = 8) -> str:
    """Fetch a UTF-8 web page without requiring a third-party HTTP package."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "HARVIS/1.0 (+local desktop assistant)"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")


def _clean_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    return " ".join(html.unescape(value).split())


def _searxng_url() -> str:
    """Return the locally hosted SearXNG endpoint, if the user configured one."""
    return os.environ.get("SEARXNG_URL", "http://localhost:8080").rstrip("/")


def _format_search_results(results: list[dict], limit: int) -> str:
    lines = []
    for index, item in enumerate(results[:limit], start=1):
        title = _clean_html(str(item.get("title") or "Untitled result"))
        url = str(item.get("url") or "").strip()
        snippet = _clean_html(str(item.get("content") or ""))
        if not url:
            continue
        line = f"{index}. {title}\n   {url}"
        if snippet:
            line += f"\n   {snippet[:300]}"
        lines.append(line)
    return "\n".join(lines)


def _with_source_page(results: list[dict], output: str, fetch_content: bool) -> str:
    """Include the first result's readable page text without opening a browser."""
    if not fetch_content:
        return output
    source_url = next((str(item.get("url") or "").strip() for item in results if item.get("url")), "")
    if not source_url:
        return output
    page = fetch_url(source_url, max_chars=6000)
    if page["success"]:
        return f"{output}\n\nDetails from {source_url}:\n{page['output']}"
    return f"{output}\n\nCould not retrieve the first source page: {page['error']}"


def _searxng_results(query: str) -> list[dict]:
    url = f"{_searxng_url()}/search?{urllib.parse.urlencode({'q': query, 'format': 'json'})}"
    data = json.loads(_download(url))
    results = data.get("results", [])
    return results if isinstance(results, list) else []


def _first_media_url(results: list[dict], service: str) -> tuple[str, str] | None:
    for result in results:
        url = str(result.get("url") or "").strip()
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if service == "youtube":
            is_track = (
                host == "youtu.be"
                or host.endswith("youtube.com") and (parsed.path == "/watch" or parsed.path.startswith("/shorts/"))
            )
        else:
            is_track = host == "open.spotify.com" and parsed.path.startswith("/track/")
        if is_track:
            return url, _clean_html(str(result.get("title") or ""))
    return None


def play_song(query: str):
    """Find a direct track URL via SearXNG and open it in the default browser."""
    if not query or not query.strip():
        return {"success": False, "error": "A song title or artist is required."}

    query = query.strip()
    searches = [
        ("youtube", f'"{query}" site:youtube.com/watch'),
        ("spotify", f'"{query}" site:open.spotify.com/track'),
    ]
    errors = []
    for service, search_query in searches:
        try:
            media = _first_media_url(_searxng_results(search_query), service)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            errors.append(str(e))
            continue
        if not media:
            continue
        url, title = media
        opened = open_url(url)
        if not opened["success"]:
            return opened
        label = title or query
        service_label = "YouTube" if service == "youtube" else "Spotify"
        return {"success": True, "output": f"Opening '{label}' on {service_label}."}

    # If SearXNG cannot return a direct track, YouTube's own query page remains
    # the most reliable last resort. It is only used after both direct searches.
    youtube_search = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
    opened = open_url(youtube_search)
    if opened["success"]:
        return {"success": True, "output": f"No direct track was found; opened YouTube results for '{query}'."}
    detail = "; ".join(errors) or "No direct YouTube or Spotify track was found."
    return {"success": False, "error": f"Could not play '{query}': {detail}"}


def _duckduckgo_html_results(page: str, limit: int) -> list[tuple[str, str, str]]:
    """Extract title, URL, and snippet triples from DuckDuckGo's HTML results page."""
    results = []
    seen_urls = set()
    matches = list(re.finditer(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        page,
        re.DOTALL,
    ))
    for index, match in enumerate(matches):
        url, title = html.unescape(match.group(1)), _clean_html(match.group(2))
        if url.startswith("//"):
            url = f"https:{url}"
        if not url or not title or url in seen_urls:
            continue
        seen_urls.add(url)
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(page)
        result_section = page[match.end():next_start]
        snippet_match = re.search(r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|td|div)>', result_section, re.DOTALL)
        snippet = _clean_html(snippet_match.group(1)) if snippet_match else ""
        results.append((title, url, snippet))
        if len(results) >= limit:
            break
    return results


def web_search(
    query: str,
    open_in_browser: bool = False,
    max_results: int = 5,
    fetch_content: bool = True,
):
    if not query or not query.strip():
        return {"success": False, "error": "Search query is required."}

    query = query.strip()

    if open_in_browser:
        url = f"https://duckduckgo.com/?q={urllib.parse.quote(query)}"
        try:
            subprocess.Popen(
                ["xdg-open", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {"success": True, "output": f"Opened web search for '{query}' in browser."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    try:
        max_results = max(1, min(10, int(max_results)))
    except (TypeError, ValueError):
        return {"success": False, "error": "max_results must be a whole number between 1 and 10."}
    errors = []
    # A local SearXNG instance combines multiple search providers and works even
    # when DuckDuckGo's public endpoints are blocked or rate-limited.
    try:
        results = _searxng_results(query)
        output = _format_search_results(results, max_results)
        if output:
            return {"success": True, "output": _with_source_page(results, output, fetch_content)}
    except (OSError, ValueError, json.JSONDecodeError) as e:
        errors.append(f"SearXNG: {e}")
        logger.warning("SearXNG search failed: %s", e)

    try:
        api_url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1&skip_disambig=1"
        data = json.loads(_download(api_url))
        parts = []
        if data.get("AbstractText"):
            parts.append(data["AbstractText"])
            if data.get("AbstractURL"):
                parts.append(f"Source: {data['AbstractURL']}")
        if data.get("Answer"):
            parts.append(data["Answer"])
        if data.get("Definition"):
            parts.append(data["Definition"])
        if parts:
            return {"success": True, "output": "\n".join(parts)}
    except (OSError, ValueError, json.JSONDecodeError) as e:
        errors.append(f"instant-answer service: {e}")
        logger.warning("DuckDuckGo instant-answer search failed: %s", e)

    try:
        search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        results = _duckduckgo_html_results(_download(search_url), max_results)
        if results:
            lines = []
            for index, (title, url, snippet) in enumerate(results, start=1):
                line = f"{index}. {title}\n   {url}"
                if snippet:
                    line += f"\n   {snippet[:300]}"
                lines.append(line)
            result_items = [
                {"title": title, "url": url, "content": snippet}
                for title, url, snippet in results
            ]
            output = "\n".join(lines)
            return {"success": True, "output": _with_source_page(result_items, output, fetch_content)}
    except OSError as e:
        errors.append(f"results service: {e}")
        logger.warning("DuckDuckGo results search failed: %s", e)

    return {
        "success": False,
        "error": "Online search is unavailable. " + "; ".join(errors),
    }


def fetch_url(url: str, max_chars: int = 12000):
    """Fetch a public web page and return its readable text for follow-up questions."""
    try:
        parsed = urllib.parse.urlparse(url.strip())
    except (AttributeError, ValueError):
        return {"success": False, "error": "A valid HTTP or HTTPS URL is required."}
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return {"success": False, "error": "Only complete HTTP or HTTPS URLs can be fetched."}

    try:
        address = ipaddress.ip_address(parsed.hostname)
        if address.is_private or address.is_loopback or address.is_link_local:
            return {"success": False, "error": "Fetching local or private network addresses is not allowed."}
    except ValueError:
        if parsed.hostname.lower() == "localhost":
            return {"success": False, "error": "Fetching local or private network addresses is not allowed."}

    try:
        max_chars = max(500, min(30000, int(max_chars)))
    except (TypeError, ValueError):
        return {"success": False, "error": "max_chars must be a whole number between 500 and 30000."}

    try:
        page = _download(url, timeout=12)
    except OSError as e:
        return {"success": False, "error": f"Could not fetch URL: {e}"}

    # Remove non-content elements before converting HTML into readable text.
    page = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", page, flags=re.IGNORECASE | re.DOTALL)
    text = _clean_html(page)
    if not text:
        return {"success": False, "error": "The page did not contain readable text."}
    suffix = "…" if len(text) > max_chars else ""
    return {"success": True, "output": text[:max_chars] + suffix}


def _flatten_topics(topics):
    results = []
    for topic in topics:
        if isinstance(topic, dict):
            if topic.get("Text"):
                results.append(topic["Text"])
            results.extend(_flatten_topics(topic.get("Topics", [])))
    return results


def brightness_control(action: str, level: int = None):
    if shutil.which("brightnessctl"):
        try:
            if action == "get":
                result = _run_cmd(["brightnessctl", "info"])
                if result.returncode != 0:
                    return {"success": False, "error": result.stderr.strip() or "Failed to read brightness."}
                match = re.search(r"Current brightness: \d+ \((\d+)%\)", result.stdout)
                pct = match.group(1) if match else result.stdout.strip()
                return {"success": True, "output": f"Brightness: {pct}%"}

            if action == "set":
                if level is None:
                    return {"success": False, "error": "Level (0-100) is required for set action."}
                level = max(0, min(100, int(level)))
                result = _run_cmd(["brightnessctl", "set", f"{level}%"])
                if result.returncode != 0:
                    return {"success": False, "error": result.stderr.strip() or "Failed to set brightness."}
                return {"success": True, "output": f"Brightness set to {level}%."}

            if action == "max":
                result = _run_cmd(["brightnessctl", "set", "100%"])
                if result.returncode != 0:
                    return {"success": False, "error": result.stderr.strip() or "Failed to set brightness."}
                return {"success": True, "output": "Brightness set to maximum (100%)."}

            if action == "min":
                result = _run_cmd(["brightnessctl", "set", "0%"])
                if result.returncode != 0:
                    return {"success": False, "error": result.stderr.strip() or "Failed to set brightness."}
                return {"success": True, "output": "Brightness set to minimum (0%)."}

            return {"success": False, "error": f"Unknown brightness action: {action}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    backlight_dirs = glob.glob("/sys/class/backlight/*")
    if not backlight_dirs:
        return {"success": False, "error": "No brightness control available (install brightnessctl)."}

    device = backlight_dirs[0]
    brightness_path = os.path.join(device, "brightness")
    max_path = os.path.join(device, "max_brightness")

    try:
        with open(max_path) as f:
            max_val = int(f.read().strip())

        if action == "get":
            with open(brightness_path) as f:
                current = int(f.read().strip())
            pct = round(current / max_val * 100)
            return {"success": True, "output": f"Brightness: {pct}%"}

        if action == "set":
            if level is None:
                return {"success": False, "error": "Level (0-100) is required for set action."}
            level = max(0, min(100, int(level)))
            target = round(max_val * level / 100)
            with open(brightness_path, "w") as f:
                f.write(str(target))
            return {"success": True, "output": f"Brightness set to {level}%."}

        if action == "max":
            with open(brightness_path, "w") as f:
                f.write(str(max_val))
            return {"success": True, "output": "Brightness set to maximum (100%)."}

        if action == "min":
            with open(brightness_path, "w") as f:
                f.write("0")
            return {"success": True, "output": "Brightness set to minimum (0%)."}

        return {"success": False, "error": f"Unknown brightness action: {action}"}
    except PermissionError:
        return {"success": False, "error": "Permission denied adjusting brightness. Install brightnessctl or run with appropriate permissions."}
    except Exception as e:
        return {"success": False, "error": str(e)}


def media_control(action: str):
    if not shutil.which("playerctl"):
        return {
            "success": False,
            "error": "playerctl is not installed. Install with: sudo pacman -S playerctl",
        }

    cmd_map = {
        "play": ["playerctl", "play"],
        "pause": ["playerctl", "pause"],
        "play_pause": ["playerctl", "play-pause"],
        "next": ["playerctl", "next"],
        "previous": ["playerctl", "previous"],
        "stop": ["playerctl", "stop"],
    }

    try:
        if action == "status":
            title = _run_cmd(["playerctl", "metadata", "title"])
            artist = _run_cmd(["playerctl", "metadata", "artist"])
            status = _run_cmd(["playerctl", "status"])
            if title.returncode != 0 and status.returncode != 0:
                return {"success": True, "output": "No active media player."}
            parts = []
            if status.returncode == 0 and status.stdout.strip():
                parts.append(f"Status: {status.stdout.strip()}")
            if title.returncode == 0 and title.stdout.strip():
                parts.append(f"Title: {title.stdout.strip()}")
            if artist.returncode == 0 and artist.stdout.strip():
                parts.append(f"Artist: {artist.stdout.strip()}")
            return {"success": True, "output": "\n".join(parts) if parts else "No track info available."}

        if action not in cmd_map:
            return {"success": False, "error": f"Unknown media action: {action}"}

        result = _run_cmd(cmd_map[action])
        if result.returncode != 0:
            err = result.stderr.strip() or "No active media player."
            return {"success": False, "error": err}
        labels = {
            "play": "Playback started.",
            "pause": "Playback paused.",
            "play_pause": "Toggled play/pause.",
            "next": "Skipped to next track.",
            "previous": "Skipped to previous track.",
            "stop": "Playback stopped.",
        }
        return {"success": True, "output": labels[action]}
    except Exception as e:
        return {"success": False, "error": str(e)}


def screenshot(path: str = None, copy_to_clipboard: bool = False):
    pictures = os.path.expanduser("~/Pictures")
    os.makedirs(pictures, exist_ok=True)

    if not path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(pictures, f"HARVIS-screenshot-{stamp}.png")
    else:
        path = os.path.abspath(os.path.expanduser(path))
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    wayland = session == "wayland" or os.environ.get("WAYLAND_DISPLAY")

    candidates = []
    if wayland:
        if shutil.which("grim"):
            candidates.append(["grim", path])
        if shutil.which("grimblast"):
            candidates.append(["grimblast", "save", path])
    if shutil.which("spectacle"):
        candidates.append(["spectacle", "-b", "-n", "-o", path])
    if shutil.which("scrot"):
        candidates.append(["scrot", path])

    if not candidates:
        return {"success": False, "error": "No screenshot tool available (install grim, spectacle, or scrot)."}

    errors = []
    try:
        for cmd in candidates:
            result = _run_cmd(cmd, timeout=15)
            if result.returncode == 0 and os.path.exists(path):
                output = f"Screenshot saved to {path}"
                if copy_to_clipboard:
                    clip_result = clipboard(action="write", content=path)
                    if clip_result["success"]:
                        output += " (path copied to clipboard)"
                return {"success": True, "output": output}
            err = result.stderr.strip() or result.stdout.strip() or "Screenshot failed."
            errors.append(f"{cmd[0]}: {err}")

        return {"success": False, "error": "; ".join(errors)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def notify(title: str, message: str = ""):
    if not shutil.which("notify-send"):
        return {"success": False, "error": "notify-send is not available on this system."}
    try:
        subprocess.run(
            ["notify-send", title, message],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return {"success": True, "output": f"Notification sent: {title}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def clipboard(action: str, content: str = None):
    if action == "read":
        for cmd in (["wl-paste"], ["xclip", "-selection", "clipboard", "-o"]):
            if shutil.which(cmd[0]):
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        text = result.stdout
                        if not text.strip():
                            return {"success": True, "output": "(clipboard is empty)"}
                        preview = text[:500] + ("..." if len(text) > 500 else "")
                        return {"success": True, "output": preview}
                except Exception:
                    continue
        return {"success": False, "error": "No clipboard tool available (need wl-paste or xclip)."}

    if action == "write":
        if content is None:
            return {"success": False, "error": "Content is required for clipboard write."}
        if shutil.which("wl-copy"):
            try:
                result = subprocess.run(
                    ["wl-copy"],
                    input=content,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    return {"success": True, "output": f"Copied {len(content)} characters to clipboard."}
            except Exception as e:
                logger.warning(f"wl-copy failed: {e}")
        if shutil.which("xclip"):
            try:
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=content,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    return {"success": True, "output": f"Copied {len(content)} characters to clipboard."}
            except Exception as e:
                logger.warning(f"xclip failed: {e}")
        return {"success": False, "error": "No clipboard tool available (need wl-copy or xclip)."}

    return {"success": False, "error": f"Unknown clipboard action: {action}"}


def volume_control(action: str, level: int = None):
    if not shutil.which("pactl"):
        return {"success": False, "error": "pactl is not available on this system."}

    try:
        if action == "get":
            result = subprocess.run(
                ["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            mute = subprocess.run(
                ["pactl", "get-sink-mute", "@DEFAULT_SINK@"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            vol = result.stdout.strip()
            muted = "yes" in mute.stdout.lower()
            return {"success": True, "output": f"Volume: {vol}{' (muted)' if muted else ''}"}

        if action == "set":
            if level is None:
                return {"success": False, "error": "Level (0-100) is required for set action."}
            level = max(0, min(100, int(level)))
            subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"],
                capture_output=True,
                timeout=5,
            )
            return {"success": True, "output": f"Volume set to {level}%."}

        if action == "mute":
            subprocess.run(
                ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"],
                capture_output=True,
                timeout=5,
            )
            return {"success": True, "output": "Audio muted."}

        if action == "unmute":
            subprocess.run(
                ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"],
                capture_output=True,
                timeout=5,
            )
            return {"success": True, "output": "Audio unmuted."}

        return {"success": False, "error": f"Unknown volume action: {action}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def list_dir(path: str = "."):
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(path):
        return {"success": False, "error": f"Path does not exist: {path}"}
    if not os.path.isdir(path):
        return {"success": False, "error": f"Not a directory: {path}"}

    try:
        entries = sorted(os.listdir(path))
        lines = []
        for entry in entries[:100]:
            full = os.path.join(path, entry)
            prefix = "d" if os.path.isdir(full) else "f"
            size = ""
            if prefix == "f":
                try:
                    size = f" ({os.path.getsize(full)} bytes)"
                except OSError:
                    pass
            lines.append(f"[{prefix}] {entry}{size}")
        suffix = f"\n... and {len(entries) - 100} more" if len(entries) > 100 else ""
        return {"success": True, "output": f"{path}:\n" + "\n".join(lines) + suffix}
    except Exception as e:
        return {"success": False, "error": str(e)}
