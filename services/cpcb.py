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
    fields = {
        _clean_key(key): value
        for key, value in record.items()
    }

    for alias in _FIELD_ALIASES[name]:
        value = fields.get(_clean_key(alias))

        if value not in (
            None,
            "",
            "NA",
            "N/A",
            "na",
            "null",
        ):
            return value

    return None


def _number(value):
    try:
        return (
            float(str(value).strip())
            if value not in (None, "", "NA", "N/A")
            else None
        )
    except (TypeError, ValueError):
        return None


def _name(value):
    return " ".join(
        str(value or "").casefold().split()
    )


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
        response = requests.get(
            Config.CPCB_API_URL,
            params=params,
            timeout=Config.REQUEST_TIMEOUT,
        )

        response.raise_for_status()
        payload = response.json()

    except (requests.RequestException, ValueError):
        return []

    records = (
        payload.get("records", [])
        if isinstance(payload, dict)
        else []
    )

    wanted_city = _name(city)
    wanted_state = _name(state)

    return [
        record
        for record in records
        if _name(_value(record, "city")) == wanted_city
        and _name(_value(record, "state"))
        in {
            wanted_state,
            "nct of " + wanted_state,
        }
    ]


def get_current(city, state):
    """
    Return normalized CPCB data for an exact city/state match.

    The data.gov.in resource can contain multiple stations for the same
    city. To keep the AQI and pollutant values internally consistent,
    this function selects the most recently updated station and uses
    that station's latest available pollutant observations.

    No pollutant is replaced with zero when data is missing.
    """

    records = _request(city, state)

    if not records:
        return None

    # ---------------------------------------------------------
    # Group records by monitoring station.
    # ---------------------------------------------------------
    grouped = {}

    for record in records:
        station = str(
            _value(record, "station") or "unknown"
        )

        timestamp = str(
            _value(record, "timestamp") or ""
        )

        grouped.setdefault(station, []).append(
            (timestamp, record)
        )

    # ---------------------------------------------------------
    # Build one normalized row for each station.
    # ---------------------------------------------------------
    station_rows = []

    for station, entries in grouped.items():

        values = {}

        pollutants = (
            "pm25",
            "pm10",
            "no2",
            "so2",
            "co",
            "o3",
        )

        for pollutant in pollutants:

            candidates = []

            for timestamp, record in entries:

                # Case 1:
                # Pollutant is directly available as a field.
                direct = _number(
                    _value(record, pollutant)
                )

                if direct is not None:
                    candidates.append(
                        (timestamp, direct)
                    )

                # Case 2:
                # Resource stores pollutant name separately.
                pollutant_id = _name(
                    record.get("pollutant_id")
                    or record.get("pollutant")
                )

                pollutant_map = {
                    "pm2.5": "pm25",
                    "pm25": "pm25",
                    "pm10": "pm10",
                    "no2": "no2",
                    "so2": "so2",
                    "co": "co",
                    "o3": "o3",
                }

                mapped_pollutant = pollutant_map.get(
                    pollutant_id
                )

                value = _number(
                    record.get("pollutant_avg")
                    or record.get("avg")
                )

                if (
                    mapped_pollutant == pollutant
                    and value is not None
                ):
                    candidates.append(
                        (timestamp, value)
                    )

            # Use the most recent observation for this
            # pollutant at this station.
            if candidates:
                values[pollutant] = max(
                    candidates,
                    key=lambda item: item[0],
                )[1]

        if values:

            latest_timestamp = max(
                entries,
                key=lambda item: item[0],
            )[0]

            station_rows.append(
                {
                    "station": station,
                    "timestamp": latest_timestamp,
                    "entries": entries,
                    "values": values,
                }
            )

    if not station_rows:
        return None

    # ---------------------------------------------------------
    # IMPORTANT:
    # Select ONE station based on the most recent timestamp.
    #
    # Previously Vayum averaged pollutant values across ALL
    # stations, while taking AQI from the latest station.
    # That could produce internally inconsistent data.
    # ---------------------------------------------------------
    selected_station = max(
        station_rows,
        key=lambda row: row["timestamp"],
    )

    station = selected_station["station"]
    timestamp = selected_station["timestamp"]
    values = selected_station["values"]
    entries = selected_station["entries"]

    # Latest record belonging to the selected station.
    latest_record = max(
        entries,
        key=lambda item: item[0],
    )[1]

    # ---------------------------------------------------------
    # Construct the final normalized result.
    # All pollutant values and AQI now refer to the same
    # monitoring station.
    # ---------------------------------------------------------
    result = {
        "pm25": values.get("pm25"),
        "pm10": values.get("pm10"),
        "no2": values.get("no2"),
        "so2": values.get("so2"),
        "co": values.get("co"),
        "o3": values.get("o3"),

        "city": (
            _value(latest_record, "city")
            or city
        ),

        "state": (
            _value(latest_record, "state")
            or state
        ),

        "station": station,

        "timestamp": (
            timestamp
            or datetime.now(timezone.utc).isoformat()
        ),

        "aqi": _number(
            _value(latest_record, "aqi")
        ),

        "source": _SOURCE,
    }

    return result