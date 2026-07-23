import unittest
from unittest.mock import patch

from main import run_brain_interaction


class FakeGui:
    def __init__(self):
        self.messages = []
        self.states = []

    def add_message(self, role, text):
        self.messages.append((role, text))

    def set_state(self, state):
        self.states.append(state)


TEXT_REPLY = {
    "type": "text",
    "content": "Hello from HARVIS.",
    "tool_name": None,
    "tool_args": None,
    "model": "test",
}


class VoiceActivationTests(unittest.TestCase):
    @patch("main.save_message")
    @patch("main.text_to_speech")
    @patch("main.ask", return_value=TEXT_REPLY)
    def test_text_input_does_not_trigger_tts(self, _ask, tts, _save):
        gui = FakeGui()

        run_brain_interaction("test", "hello", gui, speak_response=False)

        tts.assert_not_called()
        self.assertEqual(gui.messages, [("assistant", "Hello from HARVIS.")])

    @patch("main.save_message")
    @patch("main.text_to_speech")
    @patch("main.ask", return_value=TEXT_REPLY)
    def test_voice_input_triggers_tts_for_the_reply(self, _ask, tts, _save):
        gui = FakeGui()

        run_brain_interaction("test", "hello", gui, speak_response=True)

        tts.assert_called_once_with("Hello from HARVIS.", out_path="reply.mp3")
        self.assertIn("Speaking", gui.states)


if __name__ == "__main__":
    unittest.main()
