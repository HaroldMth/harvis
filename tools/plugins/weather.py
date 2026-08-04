"""Weather plugin using Open-Meteo (free, no API key required).

PLUGIN_SCHEMA defines how the registry discovers and wires this plugin.
"""

import re
import requests

PLUGIN_SCHEMA = {
    "name": "weather",
    "description": "Get current weather and forecast for a location using Open-Meteo.",
    "examples": [
        "weather in Paris",
        "what's the weather like in Tokyo?",
        "forecast for London",
        "how hot is it in Miami",
    ],
}


def handler(query: str) -> dict:
    """Main entry point called by the registry. Returns a standard tool dict."""
    match = re.search(
        r"(?:weather|forecast|temperature|how\s+(?:hot|cold|warm))?(?:\s+(?:in|for|at|like|is|it))?\s*(.+)",
        query,
        re.IGNORECASE,
    )
    if not match or not match.group(1).strip():
        return {
            "success": False,
            "error": "Please specify a location, e.g., 'weather in Paris'.",
        }

    location = match.group(1).strip().rstrip("?")
    return get_weather(location)


def get_weather(location: str, units: str = "metric") -> dict:
    """Fetch weather from Open-Meteo."""
    try:
        # 1. Geocode
        geo_url = "https://geocoding-api.open-meteo.com/v1/search"
        geo_resp = requests.get(
            geo_url, params={"name": location, "count": 1}, timeout=10
        )
        geo_data = geo_resp.json()

        if not geo_data.get("results"):
            return {"success": False, "error": f"Could not find location: {location}"}

        place = geo_data["results"][0]
        lat, lon = place["latitude"], place["longitude"]
        place_name = place.get("name", location)
        country = place.get("country", "")

        # 2. Weather data
        weather_url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": [
                "temperature_2m",
                "relative_humidity_2m",
                "apparent_temperature",
                "weather_code",
                "wind_speed_10m",
            ],
            "daily": ["temperature_2m_max", "temperature_2m_min"],
            "timezone": "auto",
        }
        if units == "imperial":
            params["temperature_unit"] = "fahrenheit"
            params["wind_speed_unit"] = "mph"

        w_resp = requests.get(weather_url, params=params, timeout=10)
        w_data = w_resp.json()

        current = w_data["current"]
        daily = w_data["daily"]

        temp = current["temperature_2m"]
        feels = current["apparent_temperature"]
        humidity = current["relative_humidity_2m"]
        wind = current["wind_speed_10m"]
        code = current["weather_code"]
        high = daily["temperature_2m_max"][0]
        low = daily["temperature_2m_min"][0]

        condition = _weather_code_to_text(code)
        unit_sym = "F" if units == "imperial" else "C"

        lines = [
            f"## :partly_cloudy_day: Weather in {place_name}{', ' + country if country else ''}",
            f"",
            f"**Condition:** {condition}",
            f"**Temperature:** {temp} deg{unit_sym} (feels like {feels} deg{unit_sym})",
            f"**Humidity:** {humidity}%",
            f"**Wind:** {wind} {'mph' if units == 'imperial' else 'km/h'}",
            f"**Today's Range:** {low} deg{unit_sym} -- {high} deg{unit_sym}",
        ]
        return {"success": True, "output": "\n".join(lines)}

    except requests.RequestException as e:
        return {"success": False, "error": f"Weather service unavailable: {e}"}
    except Exception as e:
        return {"success": False, "error": f"Error fetching weather: {e}"}


def _weather_code_to_text(code: int) -> str:
    """WMO Weather interpretation codes with icon shortcodes (no emojis)."""
    codes = {
        0:  "Clear sky :sunny:",
        1:  "Mainly clear :wb_sunny:",
        2:  "Partly cloudy :partly_cloudy_day:",
        3:  "Overcast :cloud:",
        45: "Fog :foggy:",
        48: "Depositing rime fog :foggy:",
        51: "Light drizzle :rainy:",
        53: "Moderate drizzle :rainy:",
        55: "Dense drizzle :rainy:",
        56: "Light freezing drizzle :ac_unit:",
        57: "Dense freezing drizzle :ac_unit:",
        61: "Slight rain :rainy:",
        63: "Moderate rain :rainy:",
        65: "Heavy rain :rainy_heavy:",
        66: "Light freezing rain :ac_unit:",
        67: "Heavy freezing rain :ac_unit:",
        71: "Slight snow :weather_snowy:",
        73: "Moderate snow :weather_snowy:",
        75: "Heavy snow :weather_snowy:",
        77: "Snow grains :weather_snowy:",
        80: "Slight rain showers :rainy:",
        81: "Moderate rain showers :rainy:",
        82: "Violent rain showers :rainy_heavy:",
        85: "Slight snow showers :weather_snowy:",
        86: "Heavy snow showers :weather_snowy:",
        95: "Thunderstorm :thunderstorm:",
        96: "Thunderstorm with slight hail :thunderstorm:",
        99: "Thunderstorm with heavy hail :thunderstorm:",
    }
    return codes.get(code, "Unknown :help:")
