# Vayum

Air quality monitoring and forecasting for locations across India.
College project for **Data Science with Python**.

## What it does

- Pick a state and city from all 36 states and union territories
- CPCB real-time air-quality data from India's Government Open Data Platform,
  with OpenAQ as an explicitly labelled fallback
- Official CPCB AQI when supplied; otherwise the existing CPCB-style AQI
  calculation is used and stored separately
- PM2.5, PM10, NO2, SO2, CO and O3 with graceful missing-value handling
- Last 7 days of air quality as a chart, backed by SQLite + pandas cleaning
- Next 24 hours air quality prediction from a trained scikit-learn model
- 7-day weather forecast (WeatherAPI.com, falling back to Open-Meteo)
- Responsive on desktop, laptop, tablet and mobile

The frontend/UI is unchanged from the original design. Everything below is
the backend, data pipeline and ML layer built around it.

---

## 1. Installation

Requires Python 3.10+.

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Environment variables

Copy `.env.example` to `.env` and fill in your own keys — never commit real
keys, `.env` is already in `.gitignore`.

```bash
cp .env.example .env
```

- `WEATHER_API_KEY` — free key from https://www.weatherapi.com/. If left
  blank, weather automatically falls back to Open-Meteo (no key needed,
  slightly less detailed).
- `OPENAQ_API_KEY` — free key from https://docs.openaq.org/. Used only as
  the fallback source when CPCB is unavailable.
- `CPCB_API_KEY` — API key for the official data.gov.in CPCB resource
  `3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69`. Keep it only in `.env`.
- `SECRET_KEY` — any random string, used for Flask sessions.
- `AIR_QUALITY_CACHE_MINUTES` / `WEATHER_CACHE_MINUTES` — how long a reading
  is reused before the app calls the APIs again (defaults: 60 / 120).

## 3. Database setup

```bash
python init_db.py
```

This creates `data/vayum.db` from `database/schema.sql` and geocodes every
city in `data/locations.json` (one request per city to OpenStreetMap's free
Nominatim service, ~1/second, so the first run takes a few minutes for 207
cities). Coordinates are cached — later runs are instant. You only need to
run this once (re-run any time if you edit `locations.json`).

## 4. Running the app

```bash
python app.py
```

Open `http://localhost:5000`. Selecting a city triggers, on first load:
data.gov.in CPCB (current) → OpenAQ fallback → Open-Meteo (historical weather)
→ pandas cleaning/merging → AQI processing → SQLite
cache. Subsequent visits within the cache window are served straight from
the database.

## 5. Training the ML model

The 24-hour prediction needs at least a couple of weeks of stored history
per location before it has enough lag features to train on (visiting a
city's dashboard populates 7 days at a time). Once you've browsed a handful
of cities over a few days:

```bash
python -m ml.train
```

This builds features (`ml/preprocessing.py`: calendar features, 1/2/3-day
AQI lags, 3-day rolling mean), does an 80/20 train/test split, trains a
`RandomForestRegressor` and a `LinearRegression` baseline, picks whichever
generalises better (MAE/RMSE/R², printed to the console and saved to
`models/metrics.json`), and saves it with `joblib` to
`models/aqi_model.joblib`. It also saves two plots to `ml/plots/`:
actual-vs-predicted AQI, and feature importance.

The model is **not** retrained on every request — `ml/predict.py` just loads
the saved file. Re-run `python -m ml.train` whenever you want to refresh it
with more data. If no model has been trained yet, predictions fall back to
a clearly-labelled flat "persistence" forecast instead of failing.

## 6. Project structure

```
app.py                  Flask routes only — no business logic
config.py                All environment-driven configuration

database/
  schema.sql             locations, air_quality, weather, historical_data, predictions
  db.py                   sqlite3 connection + CRUD helpers

services/
  cpcb.py                 data.gov.in CPCB real-time client (primary source)
  aqi.py                  CPCB breakpoint sub-index AQI calculation
  openaq.py               OpenAQ v3 client (fallback pollutants)
  weather.py              WeatherAPI.com client
  open_meteo.py           Open-Meteo client (keyless fallback + historical weather)

data_collection/
  locations.py            Geocoding (Nominatim) + caching
  pipeline.py             Orchestrates fetch -> pandas clean/merge -> cache -> serve

ml/
  preprocessing.py         Feature engineering (lags, rolling mean, calendar features)
  train.py                  Train/evaluate/save the model, matplotlib plots
  predict.py                Load the saved model, produce the 24h forecast

utils/errors.py            Shared exception type + clean error payloads
init_db.py                  One-off setup script
```

## 7. Complete data flow

```
User selects a city
    -> Flask route (app.py)
    -> data_collection/pipeline.py
         -> data_collection/locations.py   (lat/lon, cached)
         -> services/openaq.py             (pollutants)
         -> services/weather.py            (current + forecast)
         -> pandas/numpy cleaning, merging, interpolation
         -> database/db.py                  (SQLite cache)
         -> services/aqi.py                 (CPCB AQI)
    -> ml/predict.py                        (loads joblib model)
    -> same JSON/template shapes the original UI already expects
    -> existing Vayum templates + Chart.js
```

## 8. Error handling

Every external call (OpenAQ, WeatherAPI, Open-Meteo, Nominatim) is wrapped
so a timeout, missing key, or empty station never raises past the service
layer — it returns `None`/empty instead. `app.py` wraps each page's data
calls once more, so a full section failing shows "Unavailable" text instead
of a Flask 500 page or a stack trace. The one JSON API route
(`/api/air-quality`) returns `{"error": true, "message": "..."}` with an
appropriate HTTP status instead of leaking internals.

## 9. Notes on scope

Per the project brief, this intentionally does **not** use deep learning,
Docker, message queues, or a client-server database — just Flask, SQLite,
pandas, NumPy, scikit-learn and joblib, which is enough for a working,
explainable Data Science with Python project.
