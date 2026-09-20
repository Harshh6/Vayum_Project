"""
Vayum - database layer.

Plain sqlite3, no ORM. One connection per call (SQLite handles this fine
for a college project's traffic) with row_factory set so query results
behave like dictionaries.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from config import Config


def _now():
    return datetime.utcnow().isoformat(timespec="seconds")


@contextmanager
def get_connection():
    conn = sqlite3.connect(Config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create every table if it does not exist yet. Safe to call every boot."""
    with open(Config.SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = f.read()
    with get_connection() as conn:
        conn.executescript(schema)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(air_quality)")}
        for name, definition in (("cpcb_aqi", "INTEGER"), ("calculated_aqi", "INTEGER"), ("station", "TEXT")):
            if name not in columns:
                conn.execute(f"ALTER TABLE air_quality ADD COLUMN {name} {definition}")


# ---------------------------------------------------------------------------
# locations
# ---------------------------------------------------------------------------

def upsert_location(city, state, latitude=None, longitude=None, openaq_location_id=None):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO locations (city, state, latitude, longitude, openaq_location_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(city, state) DO UPDATE SET
                latitude = COALESCE(excluded.latitude, locations.latitude),
                longitude = COALESCE(excluded.longitude, locations.longitude),
                openaq_location_id = COALESCE(excluded.openaq_location_id, locations.openaq_location_id),
                updated_at = excluded.updated_at
            """,
            (city, state, latitude, longitude, openaq_location_id, _now()),
        )
        row = conn.execute(
            "SELECT * FROM locations WHERE city = ? AND state = ?", (city, state)
        ).fetchone()
        return dict(row) if row else None


def get_location(city, state):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM locations WHERE city = ? AND state = ?", (city, state)
        ).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# air_quality (latest / current reading cache)
# ---------------------------------------------------------------------------

def save_air_quality(location_id, reading):
    """reading: dict with recorded_at, pm25..o3, aqi, aqi_category, source."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO air_quality
                (location_id, recorded_at, pm25, pm10, no2, so2, co, o3,
                aqi, cpcb_aqi, calculated_aqi, aqi_category, station, source, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(location_id, recorded_at) DO UPDATE SET
                pm25=excluded.pm25, pm10=excluded.pm10, no2=excluded.no2,
                so2=excluded.so2, co=excluded.co, o3=excluded.o3,
                aqi=excluded.aqi, cpcb_aqi=excluded.cpcb_aqi,
                calculated_aqi=excluded.calculated_aqi,
                aqi_category=excluded.aqi_category, station=excluded.station,
                source=excluded.source, fetched_at=excluded.fetched_at
            """,
            (
                location_id, reading["recorded_at"], reading.get("pm25"),
                reading.get("pm10"), reading.get("no2"), reading.get("so2"),
                reading.get("co"), reading.get("o3"), reading.get("aqi"),
                reading.get("cpcb_aqi"),
                reading.get("calculated_aqi"), reading.get("aqi_category"),
                reading.get("station"), reading.get("source", "openaq"),
                _now(),
            ),
        )


def get_latest_air_quality(location_id, max_age_minutes=None):
    """Return the newest cached reading, or None if there isn't one
    (or it is older than max_age_minutes, when given)."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM air_quality WHERE location_id = ?
            ORDER BY recorded_at DESC LIMIT 1
            """,
            (location_id,),
        ).fetchone()
    if not row:
        return None
    row = dict(row)
    if max_age_minutes is not None:
        fetched = datetime.fromisoformat(row["fetched_at"])
        if datetime.utcnow() - fetched > timedelta(minutes=max_age_minutes):
            return None
    return row


# ---------------------------------------------------------------------------
# weather
# ---------------------------------------------------------------------------

def save_weather(location_id, entries, kind):
    """entries: list of dicts, one per day (forecast) or one item (current)."""
    with get_connection() as conn:
        for e in entries:
            conn.execute(
                """
                INSERT INTO weather
                    (location_id, recorded_at, kind, temp_c, temp_min_c, humidity,
                     wind_kph, wind_dir, pressure_mb, precip_mm, rain_chance,
                     condition, icon, source, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(location_id, recorded_at, kind) DO UPDATE SET
                    temp_c=excluded.temp_c, temp_min_c=excluded.temp_min_c,
                    humidity=excluded.humidity, wind_kph=excluded.wind_kph,
                    wind_dir=excluded.wind_dir, pressure_mb=excluded.pressure_mb,
                    precip_mm=excluded.precip_mm, rain_chance=excluded.rain_chance,
                    condition=excluded.condition, icon=excluded.icon,
                    source=excluded.source, fetched_at=excluded.fetched_at
                """,
                (
                    location_id, e["recorded_at"], kind, e.get("temp_c"),
                    e.get("temp_min_c"), e.get("humidity"), e.get("wind_kph"),
                    e.get("wind_dir"), e.get("pressure_mb"), e.get("precip_mm"),
                    e.get("rain_chance"), e.get("condition"), e.get("icon"),
                    e.get("source", "weatherapi"), _now(),
                ),
            )


def get_weather(location_id, kind, max_age_minutes=None, limit=7):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM weather WHERE location_id = ? AND kind = ?
            ORDER BY recorded_at ASC LIMIT ?
            """,
            (location_id, kind, limit),
        ).fetchall()
    rows = [dict(r) for r in rows]
    if not rows:
        return []
    if max_age_minutes is not None:
        newest_fetch = max(datetime.fromisoformat(r["fetched_at"]) for r in rows)
        if datetime.utcnow() - newest_fetch > timedelta(minutes=max_age_minutes):
            return []
    return rows


# ---------------------------------------------------------------------------
# historical_data (daily, used for charts + ML training)
# ---------------------------------------------------------------------------

def save_historical_rows(location_id, rows):
    """rows: list of dicts with date + pollutant/weather columns."""
    with get_connection() as conn:
        for r in rows:
            conn.execute(
                """
                INSERT INTO historical_data
                    (location_id, date, pm25, pm10, no2, so2, co, o3, aqi,
                     temp_c, humidity, wind_kph, pressure_mb, precip_mm, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(location_id, date) DO UPDATE SET
                    pm25=COALESCE(excluded.pm25, historical_data.pm25),
                    pm10=COALESCE(excluded.pm10, historical_data.pm10),
                    no2=COALESCE(excluded.no2, historical_data.no2),
                    so2=COALESCE(excluded.so2, historical_data.so2),
                    co=COALESCE(excluded.co, historical_data.co),
                    o3=COALESCE(excluded.o3, historical_data.o3),
                    aqi=COALESCE(excluded.aqi, historical_data.aqi),
                    temp_c=COALESCE(excluded.temp_c, historical_data.temp_c),
                    humidity=COALESCE(excluded.humidity, historical_data.humidity),
                    wind_kph=COALESCE(excluded.wind_kph, historical_data.wind_kph),
                    pressure_mb=COALESCE(excluded.pressure_mb, historical_data.pressure_mb),
                    precip_mm=COALESCE(excluded.precip_mm, historical_data.precip_mm)
                """,
                (
                    location_id, r["date"], r.get("pm25"), r.get("pm10"),
                    r.get("no2"), r.get("so2"), r.get("co"), r.get("o3"),
                    r.get("aqi"), r.get("temp_c"), r.get("humidity"),
                    r.get("wind_kph"), r.get("pressure_mb"), r.get("precip_mm"),
                    r.get("source", "mixed"),
                ),
            )


def get_historical_rows(location_id, days=7):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM historical_data WHERE location_id = ?
            ORDER BY date DESC LIMIT ?
            """,
            (location_id, days),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_all_historical_rows():
    """Every stored daily row, across every location — used to train the ML model."""
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM historical_data ORDER BY location_id, date").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# predictions
# ---------------------------------------------------------------------------

def save_predictions(location_id, generated_at, predictions, model_version):
    with get_connection() as conn:
        for p in predictions:
            conn.execute(
                """
                INSERT INTO predictions (location_id, generated_at, target_time, predicted_aqi, model_version)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(location_id, generated_at, target_time) DO UPDATE SET
                    predicted_aqi=excluded.predicted_aqi, model_version=excluded.model_version
                """,
                (location_id, generated_at, p["target_time"], p["predicted_aqi"], model_version),
            )
