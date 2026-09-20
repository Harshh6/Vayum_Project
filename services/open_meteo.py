"""
Vayum - Open-Meteo client (no API key required).

Used for:
  1. A fallback current/7-day weather forecast if WeatherAPI is not
     configured or fails.
  2. Historical daily weather, which the ML pipeline needs as training
     features (WeatherAPI's free tier does not give long history).
"""

from datetime import datetime

import requests

from config import Config

_WMO_TO_ICON = {
    range(0, 2): "sun", range(2, 4): "cloud-sun",
    range(4, 70): "cloud", range(51, 68): "rain", range(80, 100): "rain",
}


def _icon_for_wmo(code):
    if code is None:
        return "cloud"
    if code in (0, 1):
        return "sun"
    if code in (2, 3):
        return "cloud-sun"
    if code in range(51, 68) or code in range(80, 100):
        return "rain"
    return "cloud"


def _condition_for_wmo(code):
    if code is None:
        return "Unavailable"
    if code in (0, 1, 2, 3):
        return "Clear/cloudy"
    if code in (45, 48):
        return "Fog likely"
    if code in range(51, 58):
        return "Drizzle likely"
    if code in range(61, 68) or code in range(80, 83):
        return "Rain likely"
    if code in range(71, 78) or code in (85, 86):
        return "Snow likely"
    if code in range(95, 100):
        return "Thunderstorms likely"
    return "Cloudy"


def get_forecast(latitude, longitude, days=7):
    try:
        resp = requests.get(
            Config.OPEN_METEO_FORECAST_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,"
                           "wind_direction_10m,surface_pressure,precipitation,weather_code",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,"
                         "precipitation_probability_max,wind_speed_10m_max,weather_code",
                "forecast_days": min(days, 7),
                "timezone": "auto",
            },
            timeout=Config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException:
        return {"current": None, "forecast": []}

    current = payload.get("current", {})
    current_out = {
        "recorded_at": datetime.utcnow().isoformat(timespec="minutes"),
        "temp_c": current.get("temperature_2m"),
        "humidity": current.get("relative_humidity_2m"),
        "wind_kph": (current.get("wind_speed_10m") or 0) * 3.6 if current.get("wind_speed_10m") is not None else None,
        "wind_dir": current.get("wind_direction_10m"),
        "pressure_mb": current.get("surface_pressure"),
        "precip_mm": current.get("precipitation"),
        "condition": "Weather data (Open-Meteo)",
        "icon": _icon_for_wmo(current.get("weather_code")),
        "source": "open-meteo",
    }

    daily = payload.get("daily", {})
    forecast_out = []
    dates = daily.get("time", [])
    for i, date in enumerate(dates):
        weekday = datetime.strptime(date, "%Y-%m-%d").strftime("%a")
        code = daily.get("weather_code", [None] * len(dates))[i]
        forecast_out.append({
            "recorded_at": date,
            "day": "Today" if i == 0 else weekday,
            "date_display": datetime.strptime(date, "%Y-%m-%d").strftime("%d %b"),
            "temp_c": daily.get("temperature_2m_max", [None] * len(dates))[i],
            "temp_min_c": daily.get("temperature_2m_min", [None] * len(dates))[i],
            "humidity": current.get("relative_humidity_2m"),  # daily humidity not provided; reuse current
            "wind_kph": daily.get("wind_speed_10m_max", [None] * len(dates))[i],
            "precip_mm": daily.get("precipitation_sum", [None] * len(dates))[i],
            "rain_chance": daily.get("precipitation_probability_max", [None] * len(dates))[i],
            "condition": _condition_for_wmo(code),
            "icon": _icon_for_wmo(code),
            "source": "open-meteo",
        })
    return {"current": current_out, "forecast": forecast_out}


def get_current_air_quality(latitude, longitude):
    """Return modeled current air quality when no OpenAQ station is available."""
    try:
        resp = requests.get(
            Config.OPEN_METEO_AIR_QUALITY_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "pm10,pm2_5,nitrogen_dioxide,sulphur_dioxide,carbon_monoxide,ozone",
                "timezone": "auto",
            },
            timeout=Config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        current = resp.json().get("current", {})
    except requests.RequestException:
        return {}

    return {
        "pm25": current.get("pm2_5"),
        "pm10": current.get("pm10"),
        "no2": current.get("nitrogen_dioxide"),
        "so2": current.get("sulphur_dioxide"),
        "co": current.get("carbon_monoxide") / 1000 if current.get("carbon_monoxide") is not None else None,
        "o3": current.get("ozone"),
    }


def get_historical_air_quality(latitude, longitude, days=7):
    """Return daily averages of modeled air quality for ML history fallback."""
    try:
        resp = requests.get(
            Config.OPEN_METEO_AIR_QUALITY_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "hourly": "pm10,pm2_5,nitrogen_dioxide,sulphur_dioxide,carbon_monoxide,ozone",
                "past_days": days,
                "forecast_days": 0,
                "timezone": "auto",
            },
            timeout=Config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        hourly = resp.json().get("hourly", {})
    except requests.RequestException:
        return []

    daily = {}
    for index, timestamp in enumerate(hourly.get("time", [])):
        date = timestamp[:10]
        daily.setdefault(date, {key: [] for key in ("pm25", "pm10", "no2", "so2", "co", "o3")})
        source_values = {
            "pm25": hourly.get("pm2_5", []), "pm10": hourly.get("pm10", []),
            "no2": hourly.get("nitrogen_dioxide", []), "so2": hourly.get("sulphur_dioxide", []),
            "co": hourly.get("carbon_monoxide", []), "o3": hourly.get("ozone", []),
        }
        for key, values in source_values.items():
            if index < len(values) and values[index] is not None:
                value = values[index] / 1000 if key == "co" else values[index]
                daily[date][key].append(value)

    return [
        {"date": date, **{key: sum(values) / len(values) for key, values in pollutants.items() if values}}
        for date, pollutants in sorted(daily.items())
    ]


def get_historical_weather(latitude, longitude, start_date, end_date):
    """Daily historical weather between two ISO dates (YYYY-MM-DD).
    Used only by the ML training pipeline. Returns a list of dicts."""
    try:
        resp = requests.get(
            Config.OPEN_METEO_ARCHIVE_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "start_date": start_date,
                "end_date": end_date,
                "daily": "temperature_2m_mean,relative_humidity_2m_mean,"
                         "wind_speed_10m_max,surface_pressure_mean,precipitation_sum",
                "timezone": "auto",
            },
            timeout=Config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        daily = resp.json().get("daily", {})
    except requests.RequestException:
        return []

    rows = []
    dates = daily.get("time", [])
    for i, date in enumerate(dates):
        rows.append({
            "date": date,
            "temp_c": daily.get("temperature_2m_mean", [None] * len(dates))[i],
            "humidity": daily.get("relative_humidity_2m_mean", [None] * len(dates))[i],
            "wind_kph": daily.get("wind_speed_10m_max", [None] * len(dates))[i],
            "pressure_mb": daily.get("surface_pressure_mean", [None] * len(dates))[i],
            "precip_mm": daily.get("precipitation_sum", [None] * len(dates))[i],
        })
    return rows
