"""
Vayum - prediction module.

The model (ml.train) is trained on daily data and predicts the *next day's*
AQI for a location. To give the dashboard an hourly next-24-hours curve
(what the UI was built for), we:

  1. Ask the model for tomorrow's AQI from today's feature row.
  2. Build a smooth 24-point path from the current AQI to that predicted
     value using a small, deterministic diurnal adjustment (pollution
     typically dips at midday and rises in the morning/evening) - this is
     an interpolation of two real numbers, not invented data.
  3. Scale each pollutant's current value by the same ratio, so the
     pollutant chart moves consistently with the overall AQI curve.

If no model has been trained yet, the forecast is unavailable. Repeating the
current AQI would be a persistence baseline, not an ML prediction, so it must
not be presented as the requested forecast.
"""

import os
from datetime import datetime, timedelta

import joblib
import numpy as np

from config import Config
from database import db
from ml.preprocessing import build_feature_frame, latest_feature_row
from services import open_meteo

# Rough within-day multiplier: pollution tends to be higher early morning /
# evening (traffic, temperature inversion) and lower mid-afternoon.
_DIURNAL_PATTERN = np.array([
    1.05, 1.08, 1.10, 1.08, 1.02, 0.98, 0.95, 0.90, 0.88, 0.90, 0.93, 0.96,
    0.98, 1.00, 1.02, 1.03, 1.05, 1.08, 1.12, 1.15, 1.14, 1.10, 1.08, 1.06,
])


def _load_model():
    if not os.path.exists(Config.MODEL_PATH):
        return None
    return joblib.load(Config.MODEL_PATH)


def predict_next_24h(location_id, current_aqi, current_pollutants,
                     latitude=None, longitude=None, hours=24):
    """current_pollutants: dict {key: value_or_None} for the six pollutants."""
    if hours <= 0:
        return {"labels": [], "values": [], "model": "Unavailable", "pollutants": []}

    if latitude is not None and longitude is not None:
        hourly_forecast = open_meteo.get_air_quality_forecast(latitude, longitude, hours)
        if len(hourly_forecast) == hours:
            return {
                "labels": [datetime.fromisoformat(row["recorded_at"]).strftime("%H:%M")
                           for row in hourly_forecast],
                "values": [row["aqi"] for row in hourly_forecast],
                "model": "Open-Meteo hourly forecast (CPCB AQI)",
                "pollutants": [
                    {
                        "name": meta_name(key),
                        "unit": meta_unit(key),
                        "values": [
                            round(row["pollutants"].get(key), 1)
                            if row["pollutants"].get(key) is not None else None
                            for row in hourly_forecast
                        ],
                    }
                    for key in current_pollutants
                ],
            }

    bundle = _load_model()
    now = datetime.utcnow()
    labels = [(now + timedelta(hours=h)).strftime("%H:%M") for h in range(1, hours + 1)]

    if current_aqi is None:
        # Nothing to anchor a forecast to.
        return {
            "labels": labels, "values": [None] * 24,
            "model": "Unavailable — current AQI unknown",
            "pollutants": [
                {"name": meta_name(k), "unit": meta_unit(k), "values": [None] * 24}
                for k in current_pollutants
            ],
        }

    if bundle is None:
        values = [None] * 24
        model_name = "ML model unavailable — collect real history and run `python -m ml.train`"
    else:
        rows = db.get_all_historical_rows()
        df = build_feature_frame(rows)
        features = latest_feature_row(df, location_id)
        if features is None:
            values = [None] * 24
            model_name = f"{bundle['name']} unavailable for this location — not enough real history"
        else:
            next_day_aqi = float(bundle["model"].predict(features)[0])
            next_day_aqi = max(0.0, min(500.0, next_day_aqi))
            values = _interpolate_curve(current_aqi, next_day_aqi, hours)
            model_name = bundle["name"]

    pollutant_series = []
    for key, value in current_pollutants.items():
        if value is None or any(v is None for v in values):
            series = [None] * 24
        else:
            ratio_curve = np.array(values, dtype=float) / max(current_aqi, 1e-6)
            series = (ratio_curve * value).round(1).tolist()
        pollutant_series.append({"name": meta_name(key), "unit": meta_unit(key), "values": series})

    return {
        "labels": labels,
        "values": [round(v) if v is not None else None for v in values],
        "model": model_name,
        "pollutants": pollutant_series,
    }


def _interpolate_curve(current_aqi, next_day_aqi, hours=24):
    base = np.linspace(current_aqi, next_day_aqi, hours)
    pattern = np.interp(
        np.linspace(0, len(_DIURNAL_PATTERN) - 1, hours),
        np.arange(len(_DIURNAL_PATTERN)),
        _DIURNAL_PATTERN,
    )
    curve = base * pattern
    # Re-anchor so the curve's mean still matches the model's actual estimate.
    curve = curve * (np.mean([current_aqi, next_day_aqi]) / curve.mean())
    return np.clip(curve, 0, 500).tolist()


def meta_name(key):
    from data_collection.pipeline import POLLUTANT_META
    return POLLUTANT_META[key]["name"]


def meta_unit(key):
    from data_collection.pipeline import POLLUTANT_META
    return POLLUTANT_META[key]["unit"]
