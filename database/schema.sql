-- Vayum SQLite schema.
-- Kept intentionally simple: five tables, no ORM, plain SQL.

CREATE TABLE IF NOT EXISTS locations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    city        TEXT NOT NULL,
    state       TEXT NOT NULL,
    latitude    REAL,
    longitude   REAL,
    openaq_location_id INTEGER,      -- nearest OpenAQ station, if one was found
    updated_at  TEXT NOT NULL,
    UNIQUE(city, state)
);

-- Latest known reading per location (fast lookups for the dashboard).
CREATE TABLE IF NOT EXISTS air_quality (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id  INTEGER NOT NULL REFERENCES locations(id),
    recorded_at  TEXT NOT NULL,       -- ISO timestamp of the measurement
    pm25         REAL,
    pm10         REAL,
    no2          REAL,
    so2          REAL,
    co           REAL,
    o3           REAL,
    aqi          INTEGER,
    aqi_category TEXT,
    source       TEXT,                -- e.g. "openaq" or "fallback"
    fetched_at   TEXT NOT NULL,
    UNIQUE(location_id, recorded_at)
);

CREATE TABLE IF NOT EXISTS weather (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id  INTEGER NOT NULL REFERENCES locations(id),
    recorded_at  TEXT NOT NULL,       -- date (forecast day) or timestamp (current)
    kind         TEXT NOT NULL,       -- "current" or "forecast"
    temp_c       REAL,
    temp_min_c   REAL,
    humidity     REAL,
    wind_kph     REAL,
    wind_dir     TEXT,
    pressure_mb  REAL,
    precip_mm    REAL,
    rain_chance  INTEGER,
    condition    TEXT,
    icon         TEXT,
    source       TEXT,
    fetched_at   TEXT NOT NULL,
    UNIQUE(location_id, recorded_at, kind)
);

-- Longer daily history used for charts + ML training features.
CREATE TABLE IF NOT EXISTS historical_data (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id  INTEGER NOT NULL REFERENCES locations(id),
    date         TEXT NOT NULL,       -- YYYY-MM-DD
    pm25         REAL,
    pm10         REAL,
    no2          REAL,
    so2          REAL,
    co           REAL,
    o3           REAL,
    aqi          INTEGER,
    temp_c       REAL,
    humidity     REAL,
    wind_kph     REAL,
    pressure_mb  REAL,
    precip_mm    REAL,
    source       TEXT,
    UNIQUE(location_id, date)
);

CREATE TABLE IF NOT EXISTS predictions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id    INTEGER NOT NULL REFERENCES locations(id),
    generated_at   TEXT NOT NULL,
    target_time    TEXT NOT NULL,     -- the hour being predicted for
    predicted_aqi  REAL,
    model_version  TEXT,
    UNIQUE(location_id, generated_at, target_time)
);

CREATE INDEX IF NOT EXISTS idx_air_quality_location ON air_quality(location_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_weather_location ON weather(location_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_historical_location ON historical_data(location_id, date);
CREATE INDEX IF NOT EXISTS idx_predictions_location ON predictions(location_id, generated_at);
