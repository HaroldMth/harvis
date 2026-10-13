"""
Standalone test — no WebEngine, no transparency, just a plain solid
red frameless always-on-top window. If you STILL see nothing when
running this, the problem is 100% your window manager/compositor
refusing to show Tool/FramelessWindowHint/StaysOnTop windows — not
anything in gui.py or the React app.

Run with:  .venv/bin/python minimal_test.py
"""
import sys
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout
from PyQt6.QtCore import Qt

app = QApplication(sys.argv)

w = QWidget()
w.setWindowFlags(
    Qt.WindowType.FramelessWindowHint |
    Qt.WindowType.WindowStaysOnTopHint |
    Qt.WindowType.Tool
)
w.setStyleSheet("background-color: red;")

screen = QApplication.primaryScreen().geometry()
width, height = 400, 200
x = int((screen.width() - width) / 2)
y = 100
w.setGeometry(x, y, width, height)

layout = QVBoxLayout(w)
label = QLabel("IF YOU SEE THIS RED BOX, QT WINDOWS WORK FINE")
label.setStyleSheet("color: white; font-size: 16px; font-weight: bold;")
label.setWordWrap(True)
layout.addWidget(label)

w.show()
print(f"Window shown at: {w.geometry()}")
print(f"Screen geometry: {screen}")
print(f"isVisible(): {w.isVisible()}")

sys.exit(app.exec())