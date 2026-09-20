# Implementation Plan

## What the existing UI already does

- Flask, server-rendered Jinja2 templates, Chart.js for graphs — no SPA
  framework, no client-side fetch of air-quality/weather/prediction data.
- `app.py` had a "DATA LAYER" of 5 mock functions (`get_air_quality_data`,
  `calculate_overall_aqi`, `get_history_data`, `predict_air_quality`,
  `get_weather_data`) returning deterministic-but-fake data (seeded
  `random.Random`), explicitly commented as placeholders to swap out.
- Two real endpoints already existed: `/api/cities/<state>` (dependent
  dropdown) and `/api/air-quality` (JSON snapshot).
- Chart data reaches the browser by the Flask route passing a Python dict to
  `render_template`, which Jinja serialises with `|tojson` into
  `window.VAYUM` / `window.VAYUM_PREDICTION` / `window.VAYUM_HISTORY`
  `<script>` blocks; `charts.js`/`prediction.js`/`history.js` read those
  globals and draw Chart.js line charts. No new JS was needed.
- `data/locations.json` has 36 states / 207 cities but only names — no
  coordinates, so a geocoding step was required before any API could be
  called.

## Design decisions

- **Kept the exact JSON/dict shapes** the mock functions returned (same
  keys: `aqi`, `label`, `level`, `pollutants[].value`, `weather[].temp`,
  etc.) so zero JS changes were needed and only two tiny, unavoidable Jinja
  null-guards were added (a real station can lack a pollutant; the mock
  data never did).
- **SQLite over anything heavier** — matches "prefer SQLite" and "do not
  overengineer" in the brief.
- **Kept `app.py` at the project root** rather than moving to a `backend/`
  subfolder, since Flask's default static/template resolution is relative
  to the app file, and moving everything risked breaking the existing UI
  wiring for no functional benefit. Added `services/`, `database/`,
  `data_collection/`, `ml/`, `utils/` packages beside it instead.
- **RandomForest vs LinearRegression comparison**, simpler model wins unless
  the forest is meaningfully better — per the brief's "understandable +
  working, not maximum complexity."
- **Daily-granularity training data, hourly-shaped output** — OpenAQ/weather
  history is realistically daily-resolution for a free-tier college
  project; the model predicts *tomorrow's* AQI, and `ml/predict.py`
  interpolates that into the 24 hourly points the existing chart expects
  using a documented diurnal pattern (not random numbers) anchored to two
  real values (today's AQI, tomorrow's prediction).
- **Never fabricate**: every missing pollutant/weather value stays `None`
  end-to-end and templates show "N/A"/"Unavailable" rather than a made-up
  number.

## Known limitation of this environment

This sandbox has no outbound network access to OpenAQ, WeatherAPI, Open-Meteo
or Nominatim (only package registries are reachable), so live API responses
could not be exercised here. What *was* verified locally:
- Every route renders 200 end-to-end via Flask's test client, including the
  "no data available" degraded path (proves the error-handling contract).
- The CPCB AQI sub-index math against known breakpoint values.
- The full ML pipeline (`ml/train.py` → `ml/predict.py`) against seeded
  synthetic historical rows: trained a RandomForest (MAE ≈ 8 AQI points,
  R² ≈ 0.83 on the synthetic set), saved it with joblib, and reloaded it to
  produce a 24-hour forecast anchored to a live current-AQI value.

Run `python init_db.py` then `python app.py` with real API keys in `.env`
to exercise the live data path — see README.md.
