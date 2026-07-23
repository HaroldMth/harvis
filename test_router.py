import json
import unittest
from unittest.mock import patch

from brain import router


class RouterTests(unittest.TestCase):
    @patch("brain.router.set_cache")
    @patch("brain.router.update_provider_status")
    @patch("brain.router.get_history", return_value=[])
    @patch("brain.router.get_cache")
    def test_cached_tool_call_is_not_replayed(self, get_cache, _history, _status, _cache):
        get_cache.return_value = {
            "model": "old-model",
            "response": json.dumps({"type": "tool_call", "tool_name": "web_search", "tool_args": {"query": "old"}}),
        }
        fresh = {"type": "text", "content": "Hello", "tool_name": None, "tool_args": None}

        with patch.object(router, "PROVIDERS", [{"name": "fresh", "func": lambda _history: fresh.copy()}]):
            response = router.ask("test", "hi")

        self.assertEqual(response["content"], "Hello")
        self.assertEqual(response["model"], "fresh")

    @patch("brain.router.update_provider_status")
    @patch("brain.router.get_history", return_value=[])
    @patch("brain.router.get_cache", return_value=None)
    def test_provider_is_tried_without_a_failure_cooldown(self, _cache, _history, _status):
        calls = []

        def provider(_history):
            calls.append(True)
            return {"type": "text", "content": "Fresh response", "tool_name": None, "tool_args": None}

        with patch.object(router, "PROVIDERS", [{"name": "provider", "func": provider}]):
            response = router.ask("test", "hello", use_cache=False)

        self.assertEqual(calls, [True])
        self.assertEqual(response["content"], "Fresh response")


if __name__ == "__main__":
    unittest.main()
