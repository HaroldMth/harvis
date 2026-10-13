import sys
import os
import logging

from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout
from PyQt6.QtCore import Qt, QUrl, QObject, pyqtSlot, pyqtSignal, QTimer
from PyQt6.QtGui import QSurfaceFormat, QIcon
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebChannel import QWebChannel

logger = logging.getLogger("harvis.gui")


class DebugPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line, source):
        logger.info(f"[JS {level}] {message} ({source}:{line})")


class BackendBridge(QObject):
    """Signals from Python → JS."""
    state_changed   = pyqtSignal(str)          # 'idle' | 'listening' | 'thinking' | 'speaking' | 'error'
    message_received = pyqtSignal(str, str)    # role, content  (sent to chat)
    tts_available   = pyqtSignal(bool)         # whether voice toggle is enabled
    mode_changed    = pyqtSignal(str)          # 'normal' | 'think' | 'fast'

    """Slots from JS → Python."""
    # Emitted internally then connected to callbacks
    _submit_text_sig = pyqtSignal(str)
    _voice_activate_sig = pyqtSignal()

    @pyqtSlot(str)
    def submit_text(self, text: str):
        """Called by JS when user submits a chat message."""
        self._submit_text_sig.emit(text.strip())

    @pyqtSlot()
    def voice_activate(self):
        """Called by JS when user clicks the mic button."""
        self._voice_activate_sig.emit()

    @pyqtSlot(result=str)
    def get_mode(self):
        return getattr(self, "_mode", "normal")


class _ThreadSignals(QObject):
    """Marshals background-thread calls onto the Qt main thread."""
    set_state_sig    = pyqtSignal(str)
    add_message_sig  = pyqtSignal(str, str)
    set_tts_sig      = pyqtSignal(bool)
    set_mode_sig     = pyqtSignal(str)


class HarvisGUI:
    """Main application window — a proper chat UI, not an overlay."""

    def __init__(self, on_text_submit_callback=None, on_voice_activate_callback=None):
        if sys.platform.startswith("linux"):
            os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
            os.environ.setdefault(
                "QTWEBENGINE_CHROMIUM_FLAGS",
                "--enable-transparent-visuals"
            )

        self.app = QApplication.instance()
        if not self.app:
            fmt = QSurfaceFormat()
            fmt.setAlphaBufferSize(8)
            QSurfaceFormat.setDefaultFormat(fmt)
            self.app = QApplication(sys.argv)
            self.app.setApplicationName("HARVIS")

        self.on_text_submit_callback = on_text_submit_callback
        self.on_voice_activate_callback = on_voice_activate_callback

        # ── Bridge ──────────────────────────────────────────────────────────
        self.bridge = BackendBridge()
        self.bridge._submit_text_sig.connect(self._on_text_submitted)
        self.bridge._voice_activate_sig.connect(self._on_voice_activate)

        # ── Thread-safe dispatcher ───────────────────────────────────────────
        self._signals = _ThreadSignals()
        self._signals.set_state_sig.connect(self._handle_set_state)
        self._signals.add_message_sig.connect(self._handle_add_message)
        self._signals.set_tts_sig.connect(self._handle_set_tts)
        self._signals.set_mode_sig.connect(self._handle_set_mode)
        self.bridge._mode = "normal"

        # ── Main window ──────────────────────────────────────────────────────
        self.window = QMainWindow()
        self.window.setWindowTitle("HARVIS")
        self.window.resize(520, 760)
        self.window.setMinimumSize(400, 500)

        central = QWidget()
        self.window.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── WebView ──────────────────────────────────────────────────────────
        self.webview = QWebEngineView()
        self.webview.setPage(DebugPage(self.webview))

        self.channel = QWebChannel()
        self.channel.registerObject("backend", self.bridge)
        self.webview.page().setWebChannel(self.channel)

        app_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "ui", "dist", "index.html")
        )
        logger.info(f"Loading UI: {app_path}")
        self.webview.load(QUrl.fromLocalFile(app_path))
        layout.addWidget(self.webview)

        self.window.show()

    # ── Public thread-safe API (called from background threads) ─────────────

    def set_state(self, state: str):
        self._signals.set_state_sig.emit(state)

    def add_message(self, role: str, text: str):
        self._signals.add_message_sig.emit(role, text)

    def set_tts_available(self, available: bool):
        self._signals.set_tts_sig.emit(available)

    def set_mode(self, mode: str):
        self._signals.set_mode_sig.emit(mode)

    def toggle_window(self):
        """Toggle show/hide — called by F9 IPC trigger."""
        if self.window.isVisible():
            self.window.hide()
        else:
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()

    def request_confirmation_safely(self, message: str) -> bool:
        return True

    # ── Internal slots (always on Qt main thread) ────────────────────────────

    def _handle_set_state(self, state: str):
        react_map = {
            "Idle":       "idle",
            "Listening":  "listening",
            "Recording":  "listening",
            "Thinking":   "thinking",
            "Processing": "thinking",
            "Speaking":   "speaking",
            "Success":    "idle",
            "Error":      "error",
        }
        js_state = react_map.get(state, "idle")
        logger.info(f"GUI state → {state} ({js_state})")
        self.bridge.state_changed.emit(js_state)

    def _handle_add_message(self, role: str, text: str):
        logger.info(f"[{role.upper()}] {text[:60] if text else ''}")
        self.bridge.message_received.emit(role, text)

    def _handle_set_tts(self, available: bool):
        self.bridge.tts_available.emit(available)

    def _handle_set_mode(self, mode: str):
        self.bridge._mode = mode
        logger.info(f"GUI mode → {mode}")
        self.bridge.mode_changed.emit(mode)

    def _on_text_submitted(self, text: str):
        if text and self.on_text_submit_callback:
            self.on_text_submit_callback(text)

    def _on_voice_activate(self):
        if self.on_voice_activate_callback:
            self.on_voice_activate_callback()
