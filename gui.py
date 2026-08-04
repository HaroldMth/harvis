import sys
import os
import logging
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PyQt6.QtCore import Qt, QUrl, QObject, pyqtSlot, pyqtSignal, QRect, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QSurfaceFormat
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel

logger = logging.getLogger("harvis.gui")

# Sizes matching React artifact exactly
SIZES = {
    "Idle":      (96, 32),
    "Listening": (320, 88),
    "Thinking":  (180, 48),
    "Speaking":  (420, 128),
    "Error":     (420, 88),
}

class BackendBridge(QObject):
    state_changed = pyqtSignal(str)
    text_updated = pyqtSignal(str)
    on_island_clicked = pyqtSignal()

    @pyqtSlot()
    def island_clicked(self):
        self.on_island_clicked.emit()


class OverlayWindow(QWidget):
    def __init__(self, bridge):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(False)

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.webview = QWebEngineView(self)
        self.webview.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.webview.page().setBackgroundColor(Qt.GlobalColor.transparent)

        self.channel = QWebChannel()
        self.channel.registerObject("backend", bridge)
        self.webview.page().setWebChannel(self.channel)

        app_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "ui", "dist", "index.html"))
        self.webview.load(QUrl.fromLocalFile(app_path))

        self.layout.addWidget(self.webview)
        
        # Animators
        self.emerge_anim = QPropertyAnimation(self, b"geometry")
        self.emerge_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.emerge_anim.setDuration(420)
        
        self.idle_peek = 10
        self.top_margin = 14
        
        # Start tucked away
        start_w, start_h = SIZES["Idle"]
        self.setGeometry(self._rect_for(start_w, start_h, retracted=True))

    def _rect_for(self, w, h, retracted=False):
        parent_w = QApplication.primaryScreen().geometry().width()
        x = int((parent_w - w) / 2)
        y = -(h - self.idle_peek) if retracted else self.top_margin
        return QRect(x, y, w, h)
        
    def animate_to(self, w, h, retracted=False):
        self.emerge_anim.stop()
        start_rect = self.geometry()
        end_rect = self._rect_for(w, h, retracted=retracted)
        
        self.emerge_anim.setStartValue(start_rect)
        self.emerge_anim.setEndValue(end_rect)
        self.emerge_anim.start()


class HarvisGUI:
    def __init__(self, on_text_submit_callback=None, on_voice_activate_callback=None):
        if sys.platform.startswith("linux"):
            os.environ["QT_QPA_PLATFORM"] = "xcb"
            os.environ["QT_QPA_PLATFORMTHEME"] = "qt5ct"

        self.app = QApplication.instance()
        self._owns_app = False
        if not self.app:
            fmt = QSurfaceFormat()
            fmt.setAlphaBufferSize(8)
            QSurfaceFormat.setDefaultFormat(fmt)
            self.app = QApplication(sys.argv)
            self._owns_app = True

        self.on_voice_activate_callback = on_voice_activate_callback

        self.bridge = BackendBridge()
        self.bridge.on_island_clicked.connect(self.on_voice_activate)

        self.overlay = OverlayWindow(self.bridge)
        self.overlay.show()

    def set_state(self, state):
        logger.info(f"GUI State set to: {state}")
        state_map = {
            "Idle": "idle",
            "Listening": "listening",
            "Thinking": "thinking",
            "Speaking": "speaking",
            "Error": "error"
        }
        react_state = state_map.get(state, "idle")
        self.bridge.state_changed.emit(react_state)
        
        # Animate the window native geometry
        target_w, target_h = SIZES.get(state, SIZES["Idle"])
        retracted = (state == "Idle")
        self.overlay.animate_to(target_w, target_h, retracted)

    def add_message(self, role, text):
        if role == "assistant":
            self.bridge.text_updated.emit(text)
            # Adjust window height for text roughly
            lines = max(1, text.count('\n') + (len(text) // 50))
            target_w = SIZES["Speaking"][0]
            target_h = 128 + (lines * 18)
            target_h = min(target_h, 800)
            self.overlay.animate_to(target_w, target_h, retracted=False)
            
        elif role == "system" or role == "tool":
            logger.info(f"[{role.upper()}] {text}")

    def on_voice_activate(self):
        if self.on_voice_activate_callback:
            self.on_voice_activate_callback()

    def request_confirmation_safely(self, message: str) -> bool:
        return True
