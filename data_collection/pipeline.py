"""
Vayum - data collection pipeline.

This is the single place app.py talks to. For a given (city, state) it:
  1. Resolves lat/lon (data_collection.locations, cached).
  2. Checks the SQLite cache; if fresh enough, skips the API calls entirely
     (see config.AIR_QUALITY_CACHE_MINUTES / WEATHER_CACHE_MINUTES).
  3. Otherwise calls OpenAQ + WeatherAPI/Open-Meteo, cleans the response
     with pandas/numpy, stores it, and returns it.

Every function degrades gracefully: a missing API key, a station with no
data, or a network failure never raises to the caller - it returns partial
data with None where a value genuinely could not be obtained.
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from config import Config
from database import db
from data_collection.locations import get_coordinates
from services import openaq, weather as weather_service
from services.aqi import calculate_overall_aqi, category_for, HEALTH_NOTES

POLLUTANT_META = {
    "pm25": {"name": "PM2.5", "unit": "µg/m³", "limit": 250,
             "about": "Fine dust that reaches deep into the lungs"},
    "pm10": {"name": "PM10", "unit": "µg/m³", "limit": 350,
             "about": "Coarse road and construction dust"},
    "no2": {"name": "NO\u2082", "unit": "µg/m³", "limit": 180,
            "about": "Mostly from vehicle exhaust"},
    "so2": {"name": "SO\u2082", "unit": "µg/m³", "limit": 180,
            "about": "From industry and fuel burning"},
    "co": {"name": "CO", "unit": "mg/m³", "limit": 10,
           "about": "Incomplete combustion of fuel"},
    "o3": {"name": "O\u2083", "unit": "µg/m³", "limit": 180,
           "about": "Forms in sunlight, peaks in afternoon"},
}


def _location_row(city, state):
    lat, lon = get_coordinates(city, state)
    row = db.get_location(city, state)
    return row, lat, lon


def _ensure_openaq_station(location_row, lat, lon):
    """Look up (and cache) the nearest OpenAQ station id for this location."""
    if location_row and location_row.get("openaq_location_id"):
        return location_row["openaq_location_id"]
    if lat is None or lon is None:
        return None
    station = openaq.find_nearest_station(lat, lon)
    if not station:
        return None
    station_id = station.get("id")
    db.upsert_location(location_row["city"], location_row["state"], openaq_location_id=station_id)
    return station_id


def _build_pollutants_payload(values):
    """values: {"pm25": 42.0, "no2": None, ...} -> the list shape templates expect."""
    pollutants = []
    for key, meta in POLLUTANT_META.items():
        value = values.get(key)
        pollutants.append({
            "key": key, "name": meta["name"], "unit": meta["unit"],
            "limit": meta["limit"], "about": meta["about"],
            "value": round(value, 1) if value is not None else None,
        })
    return pollutants


def get_air_quality_data(city, state):
    """Current air quality, matching the shape the dashboard template expects."""
    location_row, lat, lon = _location_row(city, state)
    if not location_row:
        location_row = db.upsert_location(city, state, latitude=lat, longitude=lon)
    location_id = location_row["id"]

    cached = db.get_latest_air_quality(location_id, max_age_minutes=Config.AIR_QUALITY_CACHE_MINUTES)
    if cached and cached.get("aqi") is not None:
        values = {k: cached.get(k) for k in POLLUTANT_META}
        aqi, aqi_category = cached.get("aqi"), cached.get("aqi_category")
        recorded_at = cached["recorded_at"]
    else:
        station_id = _ensure_openaq_station(location_row, lat, lon)
        values = openaq.get_current_measurements(station_id) if station_id else {}
        has_openaq_values = any(value is not None for value in values.values())
        source = "openaq" if has_openaq_values else "open-meteo-air-quality"
        if not has_openaq_values and lat is not None and lon is not None:
            from services import open_meteo
            values = open_meteo.get_current_air_quality(lat, lon)
        values = {k: values.get(k) for k in POLLUTANT_META}  # ensure every key exists
        aqi, label, level, main_pollutant = calculate_overall_aqi(values)
        aqi_category = level
        recorded_at = datetime.utcnow().isoformat(timespec="minutes")
        db.save_air_quality(location_id, {
            "recorded_at": recorded_at, **values,
            "aqi": aqi, "aqi_category": aqi_category, "source": source,
        })

    aqi_val, label, level, main_key = calculate_overall_aqi(values)
    pollutants = _build_pollutants_payload(values)
    main_name = POLLUTANT_META[main_key]["name"] if main_key else "N/A"

    return {
        "aqi": aqi_val,
        "label": label,
        "level": level,
        "health_note": HEALTH_NOTES.get(level, "Air-quality data is currently unavailable for this location."),
        "main_pollutant": main_name,
        "pollutants": pollutants,
        "updated": datetime.fromisoformat(recorded_at).strftime("%d %b %Y, %I:%M %p"),
    }


def get_history_data(city, state, days=7):
    """Last `days` days of daily-average AQI, for the history chart."""
    location_row, lat, lon = _location_row(city, state)
    if not location_row:
        location_row = db.upsert_location(city, state, latitude=lat, longitude=lon)
    location_id = location_row["id"]

    rows = db.get_historical_rows(location_id, days=days)
    have_dates = {r["date"] for r in rows}
    today = datetime.utcnow().date()
    wanted_dates = {(today - timedelta(days=i)).isoformat() for i in range(days)}

    if not wanted_dates.issubset(have_dates):
        station_id = _ensure_openaq_station(location_row, lat, lon)
        pollutant_rows = openaq.get_historical_measurements(station_id, days=days) if station_id else []
        if not pollutant_rows and lat is not None and lon is not None:
            from services import open_meteo
            pollutant_rows = open_meteo.get_historical_air_quality(lat, lon, days=days)
        weather_rows = []
        if lat is not None and lon is not None:
            from services import open_meteo
            start = (today - timedelta(days=days - 1)).isoformat()
            weather_rows = open_meteo.get_historical_weather(lat, lon, start, today.isoformat())

        merged = _merge_history_with_pandas(pollutant_rows, weather_rows)
        if merged:
            db.save_historical_rows(location_id, merged)
        rows = db.get_historical_rows(location_id, days=days)

    labels, values = [], []
    for r in rows:
        labels.append(datetime.fromisoformat(r["date"]).strftime("%d %b"))
        values.append(r.get("aqi"))
    return {"labels": labels, "values": values}


def _merge_history_with_pandas(pollutant_rows, weather_rows):
    """Clean + merge pollutant and weather daily rows with pandas, then
    compute a daily AQI. Demonstrates: DataFrame merge, missing-value
    handling (interpolation), de-duplication, and groupby aggregation."""
    if not pollutant_rows and not weather_rows:
        return []

    df_pol = pd.DataFrame(pollutant_rows) if pollutant_rows else pd.DataFrame(columns=["date"])
    df_wx = pd.DataFrame(weather_rows) if weather_rows else pd.DataFrame(columns=["date"])

    for df in (df_pol, df_wx):
        if "date" in df.columns:
            df.drop_duplicates(subset="date", keep="last", inplace=True)

    merged = pd.merge(df_pol, df_wx, on="date", how="outer", suffixes=("", "_wx"))
    if merged.empty:
        return []

    merged.sort_values("date", inplace=True)
    pollutant_cols = [c for c in POLLUTANT_META if c in merged.columns]
    # Interpolate short pollutant gaps (e.g. one missing day) rather than
    # inventing values for a station with no data at all.
    if pollutant_cols:
        merged[pollutant_cols] = merged[pollutant_cols].interpolate(limit=2)

    rows = []
    for _, row in merged.iterrows():
        values = {k: (float(row[k]) if k in merged.columns and pd.notna(row[k]) else None)
                  for k in POLLUTANT_META}
        aqi, _, _, _ = calculate_overall_aqi(values)
        rows.append({
            "date": row["date"], **values, "aqi": aqi,
            "temp_c": row.get("temp_c"), "humidity": row.get("humidity"),
            "wind_kph": row.get("wind_kph"), "pressure_mb": row.get("pressure_mb"),
            "precip_mm": row.get("precip_mm"), "source": "openaq+open-meteo",
        })
    return rows


def predict_air_quality(city, state, hours=24):
    """Next-24-hours AQI + pollutant forecast, matching the dashboard/prediction
    page shape. Delegates the actual modelling to ml.predict."""
    from ml.predict import predict_next_24h

    location_row, lat, lon = _location_row(city, state)
    if not location_row:
        location_row = db.upsert_location(city, state, latitude=lat, longitude=lon)
    location_id = location_row["id"]

    # Refresh the real daily history before building the model feature row.
    # Without this, prediction could use stale or missing training inputs.
    get_history_data(city, state, days=7)
    current = get_air_quality_data(city, state)
    current_pollutants = {p["key"]: p["value"] for p in current["pollutants"]}
    return predict_next_24h(location_id, current["aqi"], current_pollutants)


def get_weather_data(city, state, days=7):
    """7-day forecast, matching the shape dashboard.html loops over."""
    location_row, lat, lon = _location_row(city, state)
    if not location_row:
        location_row = db.upsert_location(city, state, latitude=lat, longitude=lon)
    location_id = location_row["id"]

    cached = db.get_weather(location_id, kind="forecast", max_age_minutes=Config.WEATHER_CACHE_MINUTES, limit=days)
    if cached:
        forecast = cached
    else:
        if lat is None or lon is None:
            return []
        result = weather_service.get_weather(lat, lon, days=days)
        forecast = result.get("forecast", [])
        if result.get("current"):
            db.save_weather(location_id, [result["current"]], kind="current")
        if forecast:
            db.save_weather(location_id, forecast, kind="forecast")

    out = []
    for i, d in enumerate(forecast):
        recorded = d.get("recorded_at") or d.get("date")
        try:
            weekday = "Today" if i == 0 else datetime.fromisoformat(recorded).strftime("%a")
            date_display = datetime.fromisoformat(recorded).strftime("%d %b")
        except (ValueError, TypeError):
            weekday, date_display = d.get("day", ""), d.get("date_display", "")
        out.append({
            "day": d.get("day", weekday),
            "date": d.get("date_display", date_display),
            "temp": round(d["temp_c"]) if d.get("temp_c") is not None else None,
            "temp_min": round(d["temp_min_c"]) if d.get("temp_min_c") is not None else None,
            "condition": d.get("condition") or "Unavailable",
            "icon": d.get("icon", "cloud"),
            "humidity": round(d["humidity"]) if d.get("humidity") is not None else None,
            "wind": round(d["wind_kph"]) if d.get("wind_kph") is not None else None,
            "rain_chance": d.get("rain_chance") if d.get("rain_chance") is not None else 0,
        })
    return out
