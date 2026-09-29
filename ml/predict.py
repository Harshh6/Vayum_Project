"""
Vayum - prediction module.

Generates the next-hours AQI forecast for the dashboard.

Priority:
1. Open-Meteo hourly AQI forecast when available.
2. Trained hourly Random Forest model from ml.train.
3. Safe persistence fallback if neither is available.

The function always returns the structure expected by the dashboard.
"""

import os
from datetime import datetime, timedelta

import joblib
import numpy as np

from config import Config
from database import db
from services import open_meteo


def _load_model():
    """Load the trained model bundle if it exists."""
    if not os.path.exists(Config.MODEL_PATH):
        return None

    try:
        return joblib.load(Config.MODEL_PATH)
    except Exception:
        return None


def predict_next_24h(
    location_id,
    current_aqi,
    current_pollutants,
    latitude=None,
    longitude=None,
    hours=24,
):
    """
    Generate the next-hours AQI forecast.

    Priority:
      1. Open-Meteo hourly forecast.
      2. Trained hourly Random Forest model.
      3. Persistence fallback.
    """

    if hours <= 0:
        return {
            "labels": [],
            "values": [],
            "model": "Unavailable",
            "pollutants": [],
        }

    # =========================================================
    # 1. OPEN-METEO HOURLY FORECAST
    # =========================================================

    if latitude is not None and longitude is not None:
        try:
            hourly_forecast = open_meteo.get_air_quality_forecast(
                latitude,
                longitude,
                hours,
            )

            if (
                len(hourly_forecast) >= hours
                and all(
                    row.get("aqi") is not None
                    for row in hourly_forecast[:hours]
                )
            ):
                hourly_forecast = hourly_forecast[:hours]

                labels = []

                for row in hourly_forecast:
                    try:
                        labels.append(
                            datetime.fromisoformat(
                                row["recorded_at"]
                            ).strftime("%H:%M")
                        )
                    except (TypeError, ValueError, KeyError):
                        labels.append("")

                values = [
                    round(float(row["aqi"]))
                    for row in hourly_forecast
                ]

                pollutant_series = []

                for key in current_pollutants:
                    pollutant_series.append(
                        {
                            "name": meta_name(key),
                            "unit": meta_unit(key),
                            "values": [
                                (
                                    round(
                                        row["pollutants"].get(key),
                                        1,
                                    )
                                    if row.get("pollutants", {}).get(key)
                                    is not None
                                    else None
                                )
                                for row in hourly_forecast
                            ],
                        }
                    )

                return {
                    "labels": labels,
                    "values": values,
                    "model": "Open-Meteo hourly forecast (CPCB AQI)",
                    "pollutants": pollutant_series,
                }

        except Exception:
            # Continue to ML/persistence fallback.
            pass

    # =========================================================
    # COMMON LABELS
    # =========================================================

    now = datetime.utcnow()

    labels = [
        (now + timedelta(hours=h)).strftime("%H:%M")
        for h in range(1, hours + 1)
    ]

    # =========================================================
    # 2. TRAINED HOURLY RANDOM FOREST MODEL
    # =========================================================

    bundle = _load_model()

    values = None
    model_name = None

    if bundle is not None:
        try:
            # Current ml.train.py stores models like:
            #
            # bundle["models"][location_id]
            #
            # rather than bundle["model"].

            models = bundle.get("models", {})

            model = models.get(str(location_id))

            if model is None:
                model = models.get(location_id)

            if model is not None:

                from ml.preprocessing import latest_hourly_feature_row

                rows = db.get_historical_air_quality_hours(
                    location_id
                )

                features = latest_hourly_feature_row(
                    rows,
                    location_id,
                )

                if features is not None:

                    prediction = model.predict(features)[0]

                    prediction = np.asarray(
                        prediction,
                        dtype=float,
                    ).flatten()

                    use_model_by_horizon = bundle.get(
                        "use_model_by_horizon",
                        {},
                    )

                    use_model = (
                        use_model_by_horizon.get(
                            str(location_id)
                        )
                        or use_model_by_horizon.get(
                            location_id
                        )
                        or [True] * 24
                    )

                    values = []

                    for i in range(hours):

                        if (
                            i < len(prediction)
                            and i < len(use_model)
                            and use_model[i]
                        ):
                            predicted_value = prediction[i]
                        else:
                            predicted_value = current_aqi

                        if predicted_value is None:
                            values.append(None)
                        else:
                            predicted_value = float(
                                predicted_value
                            )

                            predicted_value = max(
                                0.0,
                                min(
                                    500.0,
                                    predicted_value,
                                ),
                            )

                            values.append(
                                round(predicted_value)
                            )

                    model_name = bundle.get(
                        "name",
                        "Hourly RandomForest",
                    )

        except Exception:
            # If the trained model cannot be used,
            # continue to persistence fallback.
            values = None
            model_name = None

    # =========================================================
    # 3. PERSISTENCE FALLBACK
    # =========================================================

    if values is None:

        if current_aqi is not None:
            try:
                base_aqi = float(current_aqi)

                base_aqi = max(
                    0.0,
                    min(
                        500.0,
                        base_aqi,
                    ),
                )

                values = [
                    round(base_aqi)
                    for _ in range(hours)
                ]

                model_name = (
                    "Persistence fallback"
                )

            except (TypeError, ValueError):
                values = [None] * hours
                model_name = (
                    "Forecast unavailable"
                )

        else:
            values = [None] * hours
            model_name = (
                "Forecast unavailable — current AQI unknown"
            )

    # =========================================================
    # 4. POLLUTANT FORECAST
    # =========================================================

    pollutant_series = []

    for key, current_value in current_pollutants.items():

        if (
            current_value is None
            or current_aqi is None
            or any(v is None for v in values)
        ):
            series = [None] * hours

        else:
            try:
                current_value = float(
                    current_value
                )

                base_aqi = max(
                    float(current_aqi),
                    1e-6,
                )

                ratio_curve = (
                    np.asarray(
                        values,
                        dtype=float,
                    )
                    / base_aqi
                )

                series = (
                    ratio_curve * current_value
                ).round(1).tolist()

            except (TypeError, ValueError):
                series = [None] * hours

        pollutant_series.append(
            {
                "name": meta_name(key),
                "unit": meta_unit(key),
                "values": series,
            }
        )

    # =========================================================
    # 5. FINAL DASHBOARD RESPONSE
    # =========================================================

    return {
        "labels": labels,
        "values": values,
        "model": model_name,
        "pollutants": pollutant_series,
    }


def meta_name(key):
    """Return the display name for a pollutant."""
    from data_collection.pipeline import POLLUTANT_META

    return POLLUTANT_META[key]["name"]


def meta_unit(key):
    """Return the display unit for a pollutant."""
    from data_collection.pipeline import POLLUTANT_META

    return POLLUTANT_META[key]["unit"]