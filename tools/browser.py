import subprocess
import shutil
import logging
import re

from tools.session import graphical_env, launch_detached

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
        launch_detached(args)
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
    if not name:
        return {"success": False, "error": "Application name is required."}

    key = name.lower().strip()

    DESKTOP_APP_MAP = {
        "settings": "systemsettings",
        "kde settings": "systemsettings",
        "kde system settings": "systemsettings",
        "system settings": "systemsettings",
        "files": "dolphin",
        "file manager": "dolphin",
        "dolphin": "dolphin",
        "antigravity": "antigravity",
    }

    app_bin = DESKTOP_APP_MAP.get(key, name)

    # 1. Explicit URL passed -> open site in browser
    if url:
        browser_cmd = resolve_browser(name)
        resolved_url = resolve_url(url) or url
        if browser_cmd and shutil.which(browser_cmd):
            res = _launch([browser_cmd, resolved_url])
            res["output"] = f"Opened {resolved_url} in {name}."
            return res
        return open_url(resolved_url)

    # 2. Local app binary on PATH -> launch directly!
    if shutil.which(app_bin):
        res = _launch([app_bin])
        res["output"] = f"Launched '{name}'."
        return res

    # 3. Known browser name -> launch browser
    browser_cmd = resolve_browser(name)
    if browser_cmd and shutil.which(browser_cmd):
        res = _launch([browser_cmd])
        res["output"] = f"Launched '{name}'."
        return res

    # 4. Known website shortcut (e.g. youtube, github, gmail) -> open in browser
    resolved_site = resolve_url(name)
    if resolved_site:
        return open_url(resolved_site)

    # 5. Shell launch fallback
    try:
        subprocess.Popen(
            app_bin,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=graphical_env(),
        )
        return {"success": True, "output": f"Launched '{name}'."}
    except Exception as e:
        return {"success": False, "error": f"Failed to launch '{name}': {e}"}


APP_PROCESS_MAP = {
    "chrome": ["google-chrome-stable", "google-chrome", "chrome", "chromium"],
    "google chrome": ["google-chrome-stable", "google-chrome", "chrome", "chromium"],
    "google-chrome": ["google-chrome-stable", "google-chrome", "chrome", "chromium"],
    "dolphin": ["dolphin"],
    "settings": ["systemsettings", "kcmshell5", "kcmshell6"],
    "system settings": ["systemsettings"],
    "systemsettings": ["systemsettings"],
    "kde settings": ["systemsettings"],
    "kde system settings": ["systemsettings"],
    "firefox": ["firefox"],
    "code": ["code", "vscode"],
    "vscode": ["code"],
    "spotify": ["spotify"],
    "terminal": ["konsole", "gnome-terminal", "alacritty", "kitty", "xterm"],
    "konsole": ["konsole"],
    "antigravity": ["antigravity"],
}


def close_app(name: str) -> dict:
    """Close, terminate, or kill running applications by name (e.g. chrome, dolphin, settings)."""
    if not name:
        return {"success": False, "error": "Application name is required."}

    raw = str(name).strip()
    items = re.split(r",\s*|\s+and\s+", raw, flags=re.IGNORECASE)
    
    closed_list = []
    for item in items:
        key = item.lower().strip()
        key = re.sub(r"^(kill|close|stop)\s+", "", key).strip()
        if not key:
            continue

        if key in ("all", "everything"):
            targets = ["google-chrome-stable", "google-chrome", "chrome", "dolphin", "systemsettings", "firefox", "code", "spotify"]
        elif key in APP_PROCESS_MAP:
            targets = APP_PROCESS_MAP[key]
        else:
            targets = [key, key.replace(" ", "-")]

        for proc in targets:
            try:
                # Use -x for exact binary name match so python runner is not killed
                res = subprocess.run(["pkill", "-x", proc], capture_output=True, text=True)
                if res.returncode == 0:
                    closed_list.append(proc)
            except Exception:
                pass

    if closed_list:
        return {"success": True, "output": f"Closed application(s): {', '.join(set(closed_list))}"}
    
    # Fallback try pkill with exact process name
    fallback_proc = raw.lower().strip().replace(" ", "-")
    try:
        res = subprocess.run(["pkill", "-x", fallback_proc], capture_output=True, text=True)
        if res.returncode == 0:
            return {"success": True, "output": f"Closed application '{fallback_proc}'."}
        return {"success": True, "output": f"Sent close signal for '{raw}'."}
    except Exception as e:
        return {"success": False, "error": f"Could not close '{raw}': {e}"}
