import json
import unittest
from unittest.mock import patch

from tools.desktop import _duckduckgo_html_results, web_search


SEARCH_HTML = '''
<a class="result__a" href="https://one.example">One &amp; Only</a>
<div class="result__snippet">First <b>search</b> result.</div>
<a class="result__a" href="https://two.example">Two</a>
<div class="result__snippet">Second result.</div>
'''


class WebSearchTests(unittest.TestCase):
    def test_extracts_titles_urls_and_snippets(self):
        self.assertEqual(
            _duckduckgo_html_results(SEARCH_HTML, 5),
            [
                ("One & Only", "https://one.example", "First search result."),
                ("Two", "https://two.example", "Second result."),
            ],
        )

    @patch("tools.desktop.fetch_url", return_value={"success": False, "error": "not needed for this test"})
    @patch("tools.desktop._download")
    def test_returns_formatted_result_list_when_no_instant_answer(self, download, _fetch_url):
        download.side_effect = [json.dumps({"results": []}), json.dumps({}), SEARCH_HTML]

        result = web_search("example query", max_results=1)

        self.assertTrue(result["success"])
        self.assertIn("1. One & Only", result["output"])
        self.assertIn("https://one.example", result["output"])
        self.assertNotIn("2. Two", result["output"])

    @patch("tools.desktop.fetch_url", return_value={"success": True, "output": "Fetched source details."})
    @patch("tools.desktop._download")
    def test_uses_searxng_results_first(self, download, fetch_url):
        download.return_value = json.dumps({
            "results": [{
                "title": "SearXNG result",
                "url": "https://example.com/result",
                "content": "A result from the local search API.",
            }]
        })

        result = web_search("example")

        self.assertTrue(result["success"])
        self.assertIn("SearXNG result", result["output"])
        self.assertIn("Fetched source details.", result["output"])
        self.assertIn("localhost:8080/search", download.call_args.args[0])
        fetch_url.assert_called_once_with("https://example.com/result", max_chars=6000)

    def test_rejects_empty_query(self):
        result = web_search("   ")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Search query is required.")

    def test_rejects_an_invalid_result_limit(self):
        result = web_search("example", max_results="many")
        self.assertFalse(result["success"])
        self.assertIn("max_results", result["error"])

    @patch("tools.desktop._download", side_effect=OSError("network unavailable"))
    def test_reports_an_error_when_search_services_are_unavailable(self, _download):
        result = web_search("example")
        self.assertFalse(result["success"])
        self.assertIn("Online search is unavailable", result["error"])

    @patch("tools.desktop._download", return_value="<html><body><h1>Useful page</h1><script>ignore()</script><p>Details here.</p></body></html>")
    def test_fetch_url_returns_readable_text(self, _download):
        from tools.desktop import fetch_url

        result = fetch_url("https://example.com/article")

        self.assertTrue(result["success"])
        self.assertEqual(result["output"], "Useful page Details here.")

    def test_fetch_url_rejects_private_address(self):
        from tools.desktop import fetch_url

        result = fetch_url("http://127.0.0.1/private")
        self.assertFalse(result["success"])
        self.assertIn("private", result["error"])

    @patch("tools.desktop.open_url", return_value={"success": True, "output": "opened"})
    @patch("tools.desktop._download")
    def test_play_song_prefers_a_direct_youtube_track(self, download, open_url):
        from tools.desktop import play_song

        download.return_value = json.dumps({"results": [{
            "title": "Artist - Song",
            "url": "https://www.youtube.com/watch?v=abc123",
        }]})
        result = play_song("Artist Song")

        self.assertTrue(result["success"])
        self.assertIn("YouTube", result["output"])
        open_url.assert_called_once_with("https://www.youtube.com/watch?v=abc123")

    @patch("tools.desktop.open_url", return_value={"success": True, "output": "opened"})
    @patch("tools.desktop._download")
    def test_play_song_falls_back_to_spotify_track(self, download, open_url):
        from tools.desktop import play_song

        download.side_effect = [
            json.dumps({"results": []}),
            json.dumps({"results": [{
                "title": "Artist - Song",
                "url": "https://open.spotify.com/track/abc123",
            }]}),
        ]
        result = play_song("Artist Song")

        self.assertTrue(result["success"])
        self.assertIn("Spotify", result["output"])
        open_url.assert_called_once_with("https://open.spotify.com/track/abc123")


if __name__ == "__main__":
    unittest.main()
