import subprocess
import shutil
import logging

logger = logging.getLogger("harvis.tools")

BROWSER_ALIASES = {
    "chrome": "google-chrome-stable",
    "google chrome": "google-chrome-stable",
    "google-chrome": "google-chrome-stable",
    "chromium": "chromium",
    "firefox": "firefox",
    "ff": "firefox",
    "brave": "brave-browser",
    "edge": "microsoft-edge",
    "opera": "opera",
    "vivaldi": "vivaldi",
}

SITE_ALIASES = {
    "youtube": "https://www.youtube.com",
    "yt": "https://www.youtube.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "reddit": "https://www.reddit.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "linkedin": "https://www.linkedin.com",
    "netflix": "https://www.netflix.com",
    "spotify web": "https://open.spotify.com",
    "gmail": "https://mail.google.com",
    "maps": "https://maps.google.com",
    "wikipedia": "https://www.wikipedia.org",
    "stackoverflow": "https://stackoverflow.com",
    "twitch": "https://www.twitch.tv",
    "discord": "https://discord.com/app",
}


def resolve_browser(name: str) -> str | None:
    if not name:
        return None
    key = name.lower().strip()
    if key in BROWSER_ALIASES:
        candidate = BROWSER_ALIASES[key]
        if shutil.which(candidate):
            return candidate
    if shutil.which(name):
        return name
    hyphenated = key.replace(" ", "-")
    if shutil.which(hyphenated):
        return hyphenated
    return BROWSER_ALIASES.get(key, name)


def resolve_url(url_or_site: str) -> str | None:
    if not url_or_site:
        return None
    s = url_or_site.strip()
    key = s.lower().rstrip("/")
    if key in SITE_ALIASES:
        return SITE_ALIASES[key]
    if s.startswith("http://") or s.startswith("https://"):
        return s
    if "." in s and " " not in s:
        return f"https://{s}"
    return None


def _launch(args: list[str]) -> dict:
    try:
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"success": True, "output": f"Launched: {' '.join(args)}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def open_url(url: str, browser: str = None):
    resolved = resolve_url(url)
    if not resolved:
        return {"success": False, "error": f"Could not resolve URL or site name: '{url}'"}

    if browser:
        cmd = resolve_browser(browser)
        if not shutil.which(cmd):
            return {"success": False, "error": f"Browser '{browser}' not found on this system."}
        result = _launch([cmd, resolved])
        if result["success"]:
            result["output"] = f"Opened {resolved} in {browser}."
        return result

    result = _launch(["xdg-open", resolved])
    if result["success"]:
        result["output"] = f"Opened {resolved} in default browser."
    return result


def open_app(name: str, url: str = None):
    browser_cmd = resolve_browser(name)
    resolved_url = resolve_url(url) if url else resolve_url(name)

    if resolved_url and browser_cmd and shutil.which(browser_cmd):
        result = _launch([browser_cmd, resolved_url])
        if result["success"]:
            result["output"] = f"Opened {resolved_url} in {name}."
        return result

    if resolved_url and not url:
        result = _launch(["xdg-open", resolved_url])
        if result["success"]:
            result["output"] = f"Opened {resolved_url} in default browser."
        return result

    if resolved_url and browser_cmd:
        result = _launch([browser_cmd, resolved_url])
        if result["success"]:
            result["output"] = f"Opened {resolved_url} in {name}."
        return result

    if browser_cmd and shutil.which(browser_cmd):
        result = _launch([browser_cmd])
        if result["success"]:
            result["output"] = f"Launched {name}."
        return result

    try:
        subprocess.Popen(
            name,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"success": True, "output": f"Launched '{name}'."}
    except Exception as e:
        return {"success": False, "error": f"Failed to launch '{name}': {e}"}
