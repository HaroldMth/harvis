import unittest
from unittest.mock import Mock, patch

from tools.plugins import weather
from tools.registry import get_all_tools_schema


class WeatherPluginTests(unittest.TestCase):
    def test_extracts_common_weather_phrases(self):
        self.assertEqual(weather._extract_location("weather in Cape Town"), "Cape Town")
        self.assertEqual(weather._extract_location("what's the weather like in Tokyo?"), "Tokyo")
        self.assertEqual(weather._extract_location("forecast for London"), "London")
        self.assertEqual(weather._extract_location("how hot is it in Miami"), "Miami")

    def test_schema_has_required_query_argument(self):
        schema = next(tool for tool in get_all_tools_schema() if tool["name"] == "weather")
        self.assertEqual(schema["parameters"]["required"], ["query"])

    @patch("tools.plugins.weather.requests.get")
    def test_fetches_and_formats_weather(self, get):
        geo_response = Mock()
        geo_response.json.return_value = {
            "results": [{"name": "Cape Town", "country": "South Africa", "latitude": -33.9, "longitude": 18.4}]
        }
        forecast_response = Mock()
        forecast_response.json.return_value = {
            "current": {
                "temperature_2m": 18.0,
                "apparent_temperature": 17.0,
                "relative_humidity_2m": 70,
                "weather_code": 2,
                "wind_speed_10m": 15.0,
            },
            "daily": {"temperature_2m_max": [21.0], "temperature_2m_min": [12.0]},
        }
        get.side_effect = [geo_response, forecast_response]

        result = weather.get_weather("Cape Town")

        self.assertTrue(result["success"])
        self.assertIn("Weather in Cape Town, South Africa", result["output"])
        self.assertIn("Partly cloudy", result["output"])
        self.assertEqual(get.call_count, 2)
        geo_response.raise_for_status.assert_called_once()
        forecast_response.raise_for_status.assert_called_once()


if __name__ == "__main__":
    unittest.main()
