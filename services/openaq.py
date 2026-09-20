"""
Vayum - OpenAQ v3 client.

OpenAQ organises data as: a "location" (physical monitoring station) has
one or more "sensors", each sensor measures one parameter (pm25, no2...).
Indian station coverage is patchy, so every function here is written to
degrade gracefully: a city with no nearby station, or a station missing
some pollutants, returns partial/None data instead of raising or faking
numbers.
"""

from math import hypot
from datetime import datetime, timedelta, timezone

import requests

from config import Config

PARAM_MAP = {
    "pm25": "pm25", "pm10": "pm10", "no2": "no2",
    "so2": "so2", "co": "co", "o3": "o3",
}
# OpenAQ CO is reported in µg/m3; CPCB breakpoints for CO use mg/m3.
_UNIT_TO_MG = {"co": 1000.0}
_INDIA_LOCATIONS = None


def _sensor_map(location_id):
    data = _get(f"/locations/{location_id}")
    results = (data or {}).get("results") or []
    location = results[0] if isinstance(results, list) and results else {}
    sensors = location.get("sensors") or []
    return {
        sensor.get("id"): {
            "name": ((sensor.get("parameter") or {}).get("name") or "").lower(),
            "units": (sensor.get("parameter") or {}).get("units"),
        }
        for sensor in sensors
        if sensor.get("id") is not None
    }


def _normalise_value(parameter, value, units=None):
    if value is None:
        return None
    units = (units or "").lower().replace("μ", "µ")
    if parameter == "co":
        if units in {"µg/m3", "µg/m³", "ug/m3", "ug/m³"}:
            return value / _UNIT_TO_MG["co"]
        if units == "ppb":
            return value * 0.001145
    return value


def _headers():
    return {"X-API-Key": Config.OPENAQ_API_KEY} if Config.OPENAQ_API_KEY else {}


def _get(path, params=None):
    if not Config.OPENAQ_API_KEY:
        # No key configured -> caller should treat this source as unavailable
        # rather than get a confusing 401 from the API.
        return None
    url = f"{Config.OPENAQ_BASE_URL}{path}"
    try:
        resp = requests.get(url, headers=_headers(), params=params,
                             timeout=Config.REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return None
        return resp.json()
    except requests.RequestException:
        return None


def find_nearest_station(latitude, longitude, radius_m=25000):
    """Return the closest available OpenAQ location to a lat/lon.

    Prefer a station inside the requested radius. If the locality has no
    nearby station, fall back to the nearest station in OpenAQ's India-wide
    location list so the caller can use real nearby-region data.
    """
    data = _get("/locations", params={
        "coordinates": f"{latitude},{longitude}",
        "radius": radius_m,
        "limit": 100,
    })
    results = (data or {}).get("results") or []
    if not results:
        results = _india_locations()
    if not results:
        return None

    def distance(location):
        coordinates = location.get("coordinates") or {}
        try:
            return hypot(float(coordinates["latitude"]) - latitude,
                         float(coordinates["longitude"]) - longitude)
        except (KeyError, TypeError, ValueError):
            return float("inf")

    station = min(results, key=distance)
    station["distance_km"] = round(distance(station) * 111.2, 1)
    return station


def _india_locations():
    global _INDIA_LOCATIONS
    if _INDIA_LOCATIONS is None:
        data = _get("/locations", params={
            "country_id": "IN", "limit": 1000, "page": 1,
        })
        _INDIA_LOCATIONS = (data or {}).get("results") or []
    return _INDIA_LOCATIONS


def get_current_measurements(openaq_location_id):
    """Latest reading per sensor for a station -> {"pm25": 42.0, ...} (µg/m3 / mg/m3)."""
    sensor_map = _sensor_map(openaq_location_id)
    data = _get(f"/locations/{openaq_location_id}/latest")
    results = (data or {}).get("results") or []
    values = {}
    for r in results:
        sensor = sensor_map.get(r.get("sensorsId"), {})
        parameter = r.get("parameter") or {}
        param = (parameter.get("name") if isinstance(parameter, dict) else parameter) or sensor.get("name")
        param = (param or "").lower()
        if param in PARAM_MAP:
            value = _normalise_value(param, r.get("value"), sensor.get("units"))
            values[PARAM_MAP[param]] = value
    return values


def get_historical_measurements(openaq_location_id, days=7):
    """Daily-averaged pollutant history for the last N days.

    Returns a list of {"date": "YYYY-MM-DD", "pm25": .., ...} dicts, oldest
    first. Days/pollutants with no measurements are simply absent (never 0).
    """
    sensor_map = _sensor_map(openaq_location_id)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    start_text = start.isoformat().replace("+00:00", "Z")
    end_text = end.isoformat().replace("+00:00", "Z")

    # Aggregate manually (pandas is used downstream in data_collection.pipeline
    # once results are merged with weather - keep this module dependency-light).
    daily = {}
    for sensor_id, sensor in sensor_map.items():
        data = _get(f"/sensors/{sensor_id}/measurements", params={
            "limit": 1000,
            "datetime_from": start_text,
            "datetime_to": end_text,
        })
        for r in ((data or {}).get("results") or []):
            parameter = r.get("parameter") or {}
            param = (parameter.get("name") if isinstance(parameter, dict) else parameter) or sensor["name"]
            param = param.lower()
            period = r.get("period") or {}
            date = (period.get("datetimeFrom") or {}).get("local", "")[:10]
            if not date or param not in PARAM_MAP:
                continue
            value = _normalise_value(param, r.get("value"), sensor.get("units"))
            if value is not None:
                daily.setdefault(date, {}).setdefault(PARAM_MAP[param], []).append(value)

    rows = []
    for date in sorted(daily.keys()):
        row = {"date": date}
        for key, vals in daily[date].items():
            row[key] = sum(vals) / len(vals)
        rows.append(row)
    return rows
