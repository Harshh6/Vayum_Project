"""
Vayum - weather service.

Primary source is WeatherAPI.com (current + 7-day forecast in one call).
If no API key is configured, or the request fails, we fall back to
Open-Meteo (no key required) so the weather section still works.
Whichever source answered is recorded so the caller/DB can trace it.
"""

from datetime import datetime

import requests

from config import Config
from services import open_meteo

_ICONS = {
    "sun": ["sunny", "clear"],
    "cloud-sun": ["partly cloudy"],
    "cloud": ["cloudy", "overcast", "mist", "fog"],
    "rain": ["rain", "drizzle", "shower", "thunder"],
}


def _icon_for(condition_text):
    text = (condition_text or "").lower()
    for icon, needles in _ICONS.items():
        if any(n in text for n in needles):
            return icon
    return "cloud"


def _weatherapi_forecast(latitude, longitude, days=7):
    if not Config.WEATHER_API_KEY:
        return None
    try:
        resp = requests.get(
            f"{Config.WEATHERAPI_BASE_URL}/forecast.json",
            params={
                "key": Config.WEATHER_API_KEY,
                "q": f"{latitude},{longitude}",
                "days": min(days, 7),
                "aqi": "no",
            },
            timeout=Config.REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        payload = resp.json()
    except requests.RequestException:
        return None

    current = payload.get("current", {})
    current_out = {
        "recorded_at": datetime.utcnow().isoformat(timespec="minutes"),
        "temp_c": current.get("temp_c"),
        "humidity": current.get("humidity"),
        "wind_kph": current.get("wind_kph"),
        "wind_dir": current.get("wind_dir"),
        "pressure_mb": current.get("pressure_mb"),
        "precip_mm": current.get("precip_mm"),
        "condition": (current.get("condition") or {}).get("text"),
        "icon": _icon_for((current.get("condition") or {}).get("text")),
        "source": "weatherapi",
    }

    forecast_days = (payload.get("forecast") or {}).get("forecastday", [])
    forecast_out = []
    for i, d in enumerate(forecast_days):
        day = d.get("day", {})
        date = d.get("date")
        weekday = datetime.strptime(date, "%Y-%m-%d").strftime("%a") if date else ""
        cond_text = (day.get("condition") or {}).get("text", "")
        forecast_out.append({
            "recorded_at": date,
            "day": "Today" if i == 0 else weekday,
            "date_display": datetime.strptime(date, "%Y-%m-%d").strftime("%d %b") if date else "",
            "temp_c": day.get("maxtemp_c"),
            "temp_min_c": day.get("mintemp_c"),
            "humidity": day.get("avghumidity"),
            "wind_kph": day.get("maxwind_kph"),
            "precip_mm": day.get("totalprecip_mm"),
            "rain_chance": day.get("daily_chance_of_rain"),
            "condition": cond_text,
            "icon": _icon_for(cond_text),
            "source": "weatherapi",
        })
    return {"current": current_out, "forecast": forecast_out}


def get_weather(latitude, longitude, days=7):
    """Returns {"current": {...}, "forecast": [...]}. Falls back to
    Open-Meteo (no key needed) if WeatherAPI is unavailable."""
    result = _weatherapi_forecast(latitude, longitude, days)
    if result is not None:
        return result
    return open_meteo.get_forecast(latitude, longitude, days)
