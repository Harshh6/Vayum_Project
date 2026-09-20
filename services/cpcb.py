"""CPCB real-time air-quality client for the data.gov.in resource."""

from datetime import datetime, timezone
import re

import requests

from config import Config

RESOURCE_ID = "3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69"
_SOURCE = "cpcb"
_FIELD_ALIASES = {
    "city": ("city", "city_name"),
    "state": ("state", "state_name"),
    "station": ("station", "station_name", "location"),
    "timestamp": ("last_update", "last_updated", "timestamp", "date"),
    "aqi": ("aqi", "air_quality_index", "air_quality_index_value"),
    "pm25": ("pm25", "pm2_5", "pm2.5", "pm25_avg", "pm2_5_avg"),
    "pm10": ("pm10", "pm10_avg"),
    "no2": ("no2", "no2_avg"),
    "so2": ("so2", "so2_avg"),
    "co": ("co", "co_avg"),
    "o3": ("o3", "o3_avg"),
}


def _clean_key(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _value(record, name):
    fields = {_clean_key(key): value for key, value in record.items()}
    for alias in _FIELD_ALIASES[name]:
        value = fields.get(_clean_key(alias))
        if value not in (None, "", "NA", "N/A", "na", "null"):
            return value
    return None


def _number(value):
    try:
        return float(str(value).strip()) if value not in (None, "", "NA", "N/A") else None
    except (TypeError, ValueError):
        return None


def _name(value):
    return " ".join(str(value or "").casefold().split())


def _request(city, state):
    if not Config.CPCB_API_KEY:
        return []
    params = {
        "api-key": Config.CPCB_API_KEY,
        "format": "json",
        "limit": 1000,
        "filters[city]": city,
    }
    try:
        response = requests.get(Config.CPCB_API_URL, params=params, timeout=Config.REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return []
    records = payload.get("records", []) if isinstance(payload, dict) else []
    wanted_city, wanted_state = _name(city), _name(state)
    return [
        record for record in records
        if _name(_value(record, "city")) == wanted_city
        and _name(_value(record, "state")) in {wanted_state, "nct of " + wanted_state}
    ]


def get_current(city, state):
    """Return normalized CPCB data for an exact city/state match.

    The resource may return one row per pollutant and station. Rows are
    grouped by station and the newest valid station observations are averaged
    by pollutant; no missing value is replaced with zero.
    """
    records = _request(city, state)
    if not records:
        return None

    grouped = {}
    for record in records:
        station = str(_value(record, "station") or "unknown")
        timestamp = _value(record, "timestamp") or ""
        grouped.setdefault(station, []).append((timestamp, record))
    station_rows = []
    for station, entries in grouped.items():
        values = {}
        latest = max(entries, key=lambda item: str(item[0]))[1]
        for _, record in entries:
            for pollutant in ("pm25", "pm10", "no2", "so2", "co", "o3"):
                direct = _number(_value(record, pollutant))
                if direct is not None:
                    values[pollutant] = direct
            pollutant_id = _name(record.get("pollutant_id") or record.get("pollutant"))
            pollutant = {"pm2.5": "pm25", "pm25": "pm25", "pm10": "pm10",
                         "no2": "no2", "so2": "so2", "co": "co", "o3": "o3"}.get(pollutant_id)
            value = _number(record.get("pollutant_avg") or record.get("avg"))
            if pollutant and value is not None:
                values[pollutant] = value
        if values:
            station_rows.append((station, latest, values))
    if not station_rows:
        return None

    result = {key: None for key in ("pm25", "pm10", "no2", "so2", "co", "o3")}
    for key in result:
        values = [row[2][key] for row in station_rows if key in row[2]]
        if values:
            result[key] = sum(values) / len(values)
    latest_record = max(station_rows, key=lambda row: str(_value(row[1], "timestamp") or ""))[1]
    timestamp = _value(latest_record, "timestamp") or datetime.now(timezone.utc).isoformat()
    result.update({
        "city": _value(latest_record, "city") or city,
        "state": _value(latest_record, "state") or state,
        "station": ", ".join(sorted({row[0] for row in station_rows})),
        "timestamp": timestamp,
        "aqi": _number(_value(latest_record, "aqi")),
        "source": _SOURCE,
    })
    return result
