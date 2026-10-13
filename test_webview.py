"""
Isolates two things at once, both at y=100 (clear of any panel, proven
visible by your red-box test):

  1. An OPAQUE QWebEngineView — tests whether WebEngineView renders at
     all inside a frameless/stays-on-top/tool window, no transparency
     involved.
  2. A TRANSPARENT QWebEngineView right below it — tests specifically
     whether alpha transparency is the broken part.

Also logs platform backend + session type, since "xcb" forced under a
native Wayland session can behave very differently than under real X11.

Run with:  .venv/bin/python test_webview.py
"""
import sys
import os
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("webview-test")

if sys.platform.startswith("linux"):
    os.environ["QT_QPA_PLATFORM"] = "xcb"
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--enable-transparent-visuals --disable-gpu"

from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage

app = QApplication(sys.argv)

logger.info(f"Qt platform backend in use: {app.platformName()}")
logger.info(f"XDG_SESSION_TYPE: {os.environ.get('XDG_SESSION_TYPE')}")
logger.info(f"XDG_CURRENT_DESKTOP: {os.environ.get('XDG_CURRENT_DESKTOP')}")
logger.info(f"WAYLAND_DISPLAY: {os.environ.get('WAYLAND_DISPLAY')}")

screen = QApplication.primaryScreen().geometry()
logger.info(f"Screen geometry: {screen}")


class LoggingPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line, source):
        logger.info(f"[JS console] {message} (line {line})")


def make_window(y, translucent, html, label):
    w = QWidget()
    w.setWindowFlags(
        Qt.WindowType.FramelessWindowHint |
        Qt.WindowType.WindowStaysOnTopHint |
        Qt.WindowType.Tool
    )
    if translucent:
        w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        w.setAutoFillBackground(False)

    width, height = 420, 140
    x = int((screen.width() - width) / 2)
    w.setGeometry(x, y, width, height)

    layout = QVBoxLayout(w)
    layout.setContentsMargins(0, 0, 0, 0)

    view = QWebEngineView(w)
    page = LoggingPage(view)
    view.setPage(page)
    if translucent:
        view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        view.setStyleSheet("background: transparent;")
        page.setBackgroundColor(Qt.GlobalColor.transparent)
    view.setHtml(html)
    layout.addWidget(view)

    view.loadFinished.connect(
        lambda ok: logger.info(f"[{label}] load finished, success={ok}")
    )

    w.show()
    logger.info(f"[{label}] geometry after show: {w.geometry()}, isVisible={w.isVisible()}, winId={w.winId()}")
    return w  # keep a reference alive


opaque_html = """
<html><body style="margin:0;background:#ff3b30;
display:flex;align-items:center;justify-content:center;
font-family:sans-serif;color:white;font-size:20px;font-weight:bold;">
OPAQUE WEBVIEW — if you see this red box, WebEngineView itself works fine
</body></html>
"""

transparent_html = """
<html><body style="margin:0;background:transparent;
display:flex;align-items:center;justify-content:center;height:100vh;">
<div style="background:rgba(124,92,255,0.75);border-radius:20px;
padding:20px 30px;color:white;font-family:sans-serif;font-size:16px;
font-weight:bold;text-align:center;">
TRANSPARENT WEBVIEW<br/>if you see a purple rounded box (not a black
rectangle, not nothing) transparency works
</div>
</body></html>
"""

win1 = make_window(100, translucent=False, html=opaque_html, label="OPAQUE")
win2 = make_window(280, translucent=True, html=transparent_html, label="TRANSPARENT")

sys.exit(app.exec())