import json
import unittest

from brain.providers import map_history_to_gemini, map_history_to_openai


class ProviderHistoryTests(unittest.TestCase):
    def test_openai_mapping_discards_orphaned_tool_messages(self):
        history = [
            {"role": "tool", "content": json.dumps({"tool_name": "web_search", "result": "old result"})},
            {"role": "user", "content": "hello"},
        ]

        messages = map_history_to_openai(history)

        self.assertEqual([message["role"] for message in messages], ["system", "user"])

    def test_openai_mapping_keeps_valid_tool_call_sequence(self):
        history = [
            {"role": "user", "content": "find a thing"},
            {"role": "assistant", "content": json.dumps({
                "type": "tool_call", "tool_name": "web_search", "tool_args": {"query": "a thing"},
            })},
            {"role": "tool", "content": json.dumps({"tool_name": "web_search", "result": "result"})},
        ]

        messages = map_history_to_openai(history)

        self.assertEqual([message["role"] for message in messages], ["system", "user", "assistant", "tool"])
        self.assertEqual(messages[-1]["tool_call_id"], messages[-2]["tool_calls"][0]["id"])

    def test_gemini_mapping_discards_orphaned_tool_messages(self):
        history = [
            {"role": "tool", "content": json.dumps({"tool_name": "web_search", "result": "old result"})},
            {"role": "user", "content": "hello"},
        ]

        contents = map_history_to_gemini(history)

        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0]["role"], "user")


if __name__ == "__main__":
    unittest.main()
