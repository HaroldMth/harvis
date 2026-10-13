import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from audio import tts


class FishTTSTests(unittest.TestCase):
    @patch.dict(os.environ, {"FISH_API_KEY": "test-key", "FISH_REFERENCE_ID": "voice-id"}, clear=True)
    @patch("audio.tts._stream_fish_audio", return_value=True)
    @patch("audio.tts.requests.post")
    def test_fish_request_saves_and_plays_mp3(self, post, play_file):
        response = Mock()
        post.return_value = response
        with tempfile.NamedTemporaryFile(suffix=".mp3") as audio_file:
            self.assertTrue(tts._fish_speak("Hello", audio_file.name))

        post.assert_called_once_with(
            tts.FISH_TTS_URL,
            headers={
                "Authorization": "Bearer test-key",
                "Content-Type": "application/json",
                "model": "s2.1-pro-free",
            },
            json={
                "text": "Hello",
                "reference_id": "voice-id",
                "format": "mp3",
                "normalize": True,
                "prosody": {"speed": 1.0},
                "latency": "balanced",
                "chunk_length": 100,
            },
            timeout=60,
            stream=True,
        )

    @patch("audio.tts._play_file", return_value=True)
    @patch("audio.tts.shutil.which", return_value=None)
    def test_streaming_writes_mp3_when_ffplay_is_unavailable(self, _which, play_file):
        response = Mock()
        response.iter_content.return_value = [b"mp3-", b"data"]
        with tempfile.NamedTemporaryFile(suffix=".mp3") as audio_file:
            self.assertTrue(tts._stream_fish_audio(response, audio_file.name))
            self.assertEqual(audio_file.read(), b"mp3-data")
        play_file.assert_called_once()

    @patch.dict(os.environ, {}, clear=True)
    @patch("audio.tts.requests.post")
    def test_fish_is_skipped_without_configuration(self, post):
        self.assertFalse(tts._fish_speak("Hello", "reply.mp3"))
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
