"""Weather plugin using Open-Meteo (free, no API key required).

PLUGIN_SCHEMA defines how the registry discovers and wires this plugin.
"""

import re
import requests

PLUGIN_SCHEMA = {
    "name": "weather",
    "description": "Get the current weather and today's high/low for a location using Open-Meteo. No API key is required.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A city, town, or natural-language weather request, for example 'Cape Town' or 'weather in Tokyo'."
            }
        },
        "required": ["query"],
    },
    "examples": [
        "weather in Paris",
        "what's the weather like in Tokyo?",
        "forecast for London",
        "how hot is it in Miami",
    ],
}


def handler(args) -> dict:
    """Main entry point called by the registry. Returns a standard tool dict."""
    if isinstance(args, dict):
        query = args.get("query") or args.get("location") or args.get("city") or ""
    else:
        query = str(args or "")
    location = _extract_location(query)
    if not location:
        # Try to auto-detect from IP
        try:
            ip_resp = requests.get("https://ipapi.co/json/", timeout=5)
            ip_data = ip_resp.json()
            location = ip_data.get("city") or ip_data.get("region") or "Cape Town"
        except Exception:
            location = "Cape Town"
    return get_weather(location)


def _ip_location() -> str:
    """Auto-detect city from IP address."""
    try:
        ip_data = requests.get("https://ipapi.co/json/", timeout=5).json()
        return ip_data.get("city") or ip_data.get("region") or "Cape Town"
    except Exception:
        return "Cape Town"


def _extract_location(query: str) -> str:
    """Accept a bare location or the common natural-language weather phrasing."""
    location = (query or "").strip().rstrip("?.! ")
    if not location:
        return ""

    # Phrases that mean "use my current location" → return "" to trigger IP auto-detect
    AUTO_PHRASES = {"current location", "my location", "here", "current", "local", "my city", "where i am"}
    if location.lower() in AUTO_PHRASES:
        return ""

    # Possessive / vague user-reference phrases → auto-detect
    import re as _re
    if _re.search(r"\b(harold|user|my|harold'?s)\b.{0,20}\blocation\b", location, _re.IGNORECASE):
        return ""

    location = re.sub(r"^(?:what(?:'s| is)\s+)?(?:the\s+)?", "", location, flags=re.IGNORECASE)
    location = re.sub(r"^(?:weather|forecast|temperature)\b", "", location, flags=re.IGNORECASE)
    location = re.sub(r"^how\s+(?:hot|cold|warm)\s+(?:is\s+it\s+)?", "", location, flags=re.IGNORECASE)
    location = location.strip()
    location = re.sub(r"^(?:like|is|it|in|for|at)\s+", "", location, flags=re.IGNORECASE)
    location = re.sub(r"^(?:in|for|at)\s+", "", location, flags=re.IGNORECASE)
    return location.strip()


def get_weather(location: str, units: str = "metric") -> dict:
    """Fetch weather from Open-Meteo."""
    try:
        # 1. Geocode
        geo_url = "https://geocoding-api.open-meteo.com/v1/search"
        geo_resp = requests.get(
            geo_url, params={"name": location, "count": 1}, timeout=10
        )
        geo_resp.raise_for_status()
        geo_data = geo_resp.json()

        if not geo_data.get("results"):
            # Geocoding failed — fall back to IP-detected city
            fallback = _ip_location()
            if fallback and fallback != location:
                return get_weather(fallback, units)
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
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min",
            "timezone": "auto",
        }
        if units == "imperial":
            params["temperature_unit"] = "fahrenheit"
            params["wind_speed_unit"] = "mph"

        w_resp = requests.get(weather_url, params=params, timeout=10)
        w_resp.raise_for_status()
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
