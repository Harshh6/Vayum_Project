"""
Vayum - Open-Meteo client (no API key required).

Used for:
  1. A fallback current/7-day weather forecast if WeatherAPI is not
     configured or fails.
  2. Historical daily weather, which the ML pipeline needs as training
     features (WeatherAPI's free tier does not give long history).
"""

from datetime import datetime, timedelta

import requests

from config import Config
from services.aqi import calculate_overall_aqi

_WMO_TO_ICON = {
    range(0, 2): "sun", range(2, 4): "cloud-sun",
    range(4, 70): "cloud", range(51, 68): "rain", range(80, 100): "rain",
}
_AIR_QUALITY_COLUMNS = {
    "pm25": "pm2_5", "pm10": "pm10", "no2": "nitrogen_dioxide",
    "so2": "sulphur_dioxide", "co": "carbon_monoxide", "o3": "ozone",
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


def _get_hourly_air_quality_data(latitude, longitude, forecast_days, include_current=False,
                                 past_days=1):
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": ",".join(_AIR_QUALITY_COLUMNS.values()),
        "past_days": past_days,
        "forecast_days": forecast_days,
        "timezone": "auto",
    }
    if include_current:
        params["current"] = ",".join(_AIR_QUALITY_COLUMNS.values())
    try:
        resp = requests.get(
            Config.OPEN_METEO_AIR_QUALITY_URL,
            params=params,
            timeout=Config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError):
        return None

    hourly = payload.get("hourly") or {}
    rows = []
    for index, recorded_at in enumerate(hourly.get("time") or []):
        try:
            timestamp = datetime.fromisoformat(recorded_at)
        except (TypeError, ValueError):
            return None
        pollutants = {}
        for key, column in _AIR_QUALITY_COLUMNS.items():
            values = hourly.get(column) or []
            value = values[index] if index < len(values) else None
            if key == "co" and value is not None:
                value /= 1000
            pollutants[key] = value
        rows.append((timestamp, pollutants))
    return payload, rows


def _rolling_pollutant_values(rows, index):
    rolling_values = {}
    for key in _AIR_QUALITY_COLUMNS:
        window = 8 if key in {"co", "o3"} else 24
        minimum_samples = 6 if window == 8 else 16
        samples = [
            row[key] for _, row in rows[max(0, index - window + 1):index + 1]
            if row[key] is not None
        ]
        rolling_values[key] = (
            sum(samples) / len(samples) if len(samples) >= minimum_samples else None
        )
    return rolling_values


def get_current_air_quality(latitude, longitude):
    """Return current modeled concentrations averaged for CPCB AQI windows."""
    result = _get_hourly_air_quality_data(latitude, longitude, forecast_days=1, include_current=True)
    if result is None:
        return {}

    payload, rows = result
    current_time = (payload.get("current") or {}).get("time")
    try:
        current_timestamp = datetime.fromisoformat(current_time)
    except (TypeError, ValueError):
        try:
            utc_offset = int(payload.get("utc_offset_seconds", 0))
        except (TypeError, ValueError):
            return {}
        current_timestamp = datetime.utcnow() + timedelta(seconds=utc_offset)
        current_timestamp = current_timestamp.replace(minute=0, second=0, microsecond=0)

    current_indices = [i for i, (timestamp, _) in enumerate(rows) if timestamp <= current_timestamp]
    if not current_indices:
        return {}
    return _rolling_pollutant_values(rows, current_indices[-1])


def get_historical_air_quality(latitude, longitude, days=7):
    """Aggregate hourly modeled air quality into CPCB-ready daily inputs."""
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

    hourly_rows = []
    for index, timestamp in enumerate(hourly.get("time", [])):
        values = {}
        for key, column in _AIR_QUALITY_COLUMNS.items():
            source_values = hourly.get(column, [])
            value = source_values[index] if index < len(source_values) else None
            if key == "co" and value is not None:
                value /= 1000
            values[key] = value
        hourly_rows.append((datetime.fromisoformat(timestamp), values))

    daily_rows = []
    dates = sorted({timestamp.date().isoformat() for timestamp, _ in hourly_rows})
    for date in dates:
        day_indices = [i for i, (timestamp, _) in enumerate(hourly_rows)
                       if timestamp.date().isoformat() == date]
        daily_values = {}
        for key in _AIR_QUALITY_COLUMNS:
            if key in {"co", "o3"}:
                rolling_means = []
                for index in day_indices:
                    samples = [
                        row[key] for _, row in hourly_rows[max(0, index - 7):index + 1]
                        if row[key] is not None
                    ]
                    if len(samples) >= 6:
                        rolling_means.append(sum(samples) / len(samples))
                if rolling_means:
                    daily_values[key] = max(rolling_means)
            else:
                samples = [hourly_rows[index][1][key] for index in day_indices
                           if hourly_rows[index][1][key] is not None]
                if len(samples) >= 16:
                    daily_values[key] = sum(samples) / len(samples)
        daily_rows.append({"date": date, **daily_values, "source": "open-meteo"})
    return daily_rows


def get_historical_air_quality_hours(latitude, longitude, days=90):
    """Return hourly concentrations and rolling CPCB AQI for model training."""
    result = _get_hourly_air_quality_data(
        latitude, longitude, forecast_days=0, past_days=days
    )
    if result is None:
        return []

    _, rows = result
    hourly_rows = []
    for index, (timestamp, pollutants) in enumerate(rows):
        rolling_values = _rolling_pollutant_values(rows, index)
        has_particulate = rolling_values["pm25"] is not None or rolling_values["pm10"] is not None
        has_three_pollutants = sum(value is not None for value in rolling_values.values()) >= 3
        aqi = calculate_overall_aqi(rolling_values)[0] if has_particulate and has_three_pollutants else None
        hourly_rows.append({
            "recorded_at": timestamp.isoformat(timespec="minutes"),
            **pollutants,
            "aqi": aqi,
            "source": "open-meteo-hourly-history",
        })
    return hourly_rows


def get_air_quality_forecast(latitude, longitude, hours=24):
    """Return hourly pollutant forecasts with CPCB AQI for the next hours.

    The API's preceding day supplies the rolling history needed by CPCB's
    24-hour particulate/gas and 8-hour CO/O3 averaging windows.
    """
    if latitude is None or longitude is None or hours <= 0:
        return []

    result = _get_hourly_air_quality_data(latitude, longitude, forecast_days=2)
    if result is None:
        return []

    payload, rows = result
    try:
        utc_offset = int(payload.get("utc_offset_seconds", 0))
    except (TypeError, ValueError):
        return []

    local_now = datetime.utcnow() + timedelta(seconds=utc_offset)
    first_forecast_hour = local_now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    first_index = next((i for i, (timestamp, _) in enumerate(rows)
                        if timestamp >= first_forecast_hour), None)
    if first_index is None or len(rows) - first_index < hours:
        return []

    forecasts = []
    for index in range(first_index, first_index + hours):
        rolling_values = _rolling_pollutant_values(rows, index)

        has_particulate = rolling_values["pm25"] is not None or rolling_values["pm10"] is not None
        has_three_pollutants = sum(value is not None for value in rolling_values.values()) >= 3
        aqi = calculate_overall_aqi(rolling_values)[0] if has_particulate and has_three_pollutants else None
        forecasts.append({
            "recorded_at": rows[index][0].isoformat(),
            "aqi": aqi,
            "pollutants": rows[index][1],
        })
    return forecasts


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
