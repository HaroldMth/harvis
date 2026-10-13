"""Launch GUI apps into the user's graphical session.

HARVIS tools often run from systemd user services (Telegram, heartbeat)
which have no DISPLAY/WAYLAND_DISPLAY. Qt apps like Konsole then abort
in QGuiApplicationPrivate::createEventDispatcher. Pull session sockets
from the user runtime dir and systemctl --user environment instead.
"""

import logging
import os
import shutil
import subprocess

logger = logging.getLogger("harvis.tools")

_GRAPHICAL_KEYS = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_TYPE",
    "DBUS_SESSION_BUS_ADDRESS",
    "QT_QPA_PLATFORM",
    "XAUTHORITY",
    "HYPRLAND_INSTANCE_SIGNATURE",
    "XDG_CURRENT_DESKTOP",
    "DESKTOP_SESSION",
)


def _uid() -> int:
    try:
        return os.getuid()
    except AttributeError:
        return 1000


def _unquote_systemd_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def _systemd_user_env() -> dict:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    env = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key] = _unquote_systemd_value(value)
    return env


def graphical_env() -> dict:
    """Environment a GUI app needs to talk to the logged-in desktop."""
    env = os.environ.copy()
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{_uid()}"
    env["XDG_RUNTIME_DIR"] = runtime

    for key, value in _systemd_user_env().items():
        if key in _GRAPHICAL_KEYS and value:
            env.setdefault(key, value)

    wayland_sock = os.path.join(runtime, "wayland-0")
    if os.path.exists(wayland_sock):
        env.setdefault("WAYLAND_DISPLAY", "wayland-0")
        env.setdefault("XDG_SESSION_TYPE", "wayland")
        env.setdefault("QT_QPA_PLATFORM", "wayland;xcb")

    if os.path.exists("/tmp/.X11-unix/X0"):
        env.setdefault("DISPLAY", ":0")
        env.setdefault("XDG_SESSION_TYPE", env.get("XDG_SESSION_TYPE") or "x11")
        env.setdefault("QT_QPA_PLATFORM", env.get("QT_QPA_PLATFORM") or "xcb")

    bus = os.path.join(runtime, "bus")
    if os.path.exists(bus):
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={bus}")

    xauth = os.path.expanduser("~/.Xauthority")
    if os.path.exists(xauth):
        env.setdefault("XAUTHORITY", xauth)

    return env


def launch_detached(args: list[str]) -> None:
    """Start a GUI process on the user desktop, outside this service cgroup."""
    if not args:
        raise ValueError("launch_detached requires a command")

    env = graphical_env()
    logger.info(
        "Launching GUI %s (DISPLAY=%s WAYLAND=%s)",
        args[0],
        env.get("DISPLAY"),
        env.get("WAYLAND_DISPLAY"),
    )

    if shutil.which("systemd-run"):
        cmd = ["systemd-run", "--user", "--quiet", "--collect"]
        for key in _GRAPHICAL_KEYS:
            value = env.get(key)
            if value:
                cmd.append(f"--setenv={key}={value}")
        cmd.append("--")
        cmd.extend(args)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
        try:
            _, err = proc.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            return
        if proc.returncode == 0:
            return
        detail = (err or b"").decode("utf-8", errors="replace").strip()
        logger.warning("systemd-run launch failed (%s); falling back to Popen: %s", proc.returncode, detail)

    subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        start_new_session=True,
    )
