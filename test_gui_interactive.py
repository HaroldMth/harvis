import sys
import logging
from PyQt6.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel
from PyQt6.QtCore import Qt
from gui import HarvisGUI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

class GUIController(QWidget):
    def __init__(self, gui: HarvisGUI):
        super().__init__()
        self.gui = gui
        self.setWindowTitle("BYTE AI GUI Controller")
        self.setGeometry(100, 200, 480, 320)
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Change GUI State (Spec-Compliant Dimensions):"))
        
        # State Buttons - Row 1
        state_layout_1 = QHBoxLayout()
        for state in ["Idle", "Activating", "Listening", "Processing", "Thinking"]:
            btn = QPushButton(state)
            btn.clicked.connect(lambda checked, s=state: self.set_gui_state(s))
            state_layout_1.addWidget(btn)
        layout.addLayout(state_layout_1)

        # State Buttons - Row 2
        state_layout_2 = QHBoxLayout()
        for state in ["Speaking", "Success", "Warning", "Error", "Dismissing"]:
            btn = QPushButton(state)
            btn.clicked.connect(lambda checked, s=state: self.set_gui_state(s))
            state_layout_2.addWidget(btn)
        layout.addLayout(state_layout_2)

        # Message Input
        layout.addWidget(QLabel("Speaking Text Response (Optional):"))
        self.text_input = QLineEdit("I've opened Visual Studio Code. You can start coding now.")
        layout.addWidget(self.text_input)

        send_btn = QPushButton("Send Speech Text (Triggers Speaking state)")
        send_btn.clicked.connect(self.send_speech)
        layout.addWidget(send_btn)

        # Toggle Expansion button
        self.expansion_btn = QPushButton("Toggle Expansion (Simulate Island Click)")
        self.expansion_btn.clicked.connect(self.toggle_expansion)
        layout.addWidget(self.expansion_btn)
        
        layout.addWidget(QLabel("Status: Click expansion button or the island to see the expanded card views!"))

    def set_gui_state(self, state):
        self.gui.set_state(state)

    def send_speech(self):
        text = self.text_input.text()
        self.gui.add_message("assistant", text)
        self.gui.set_state("Speaking")

    def toggle_expansion(self):
        # Toggle expansion via GUI bridge helper
        self.gui._on_island_clicked()

def mock_voice_activate():
    print("Voice activation triggered!")

def main():
    gui = HarvisGUI(on_voice_activate_callback=mock_voice_activate)
    controller = GUIController(gui)
    controller.show()
    sys.exit(gui.app.exec())

if __name__ == "__main__":
    main()
