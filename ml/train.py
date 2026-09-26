"""
Vayum - ML training pipeline.

Run standalone, offline, whenever you want to (re)train the model:
    python -m ml.train

Pipeline: raw historical_data rows -> pandas cleaning/feature engineering
(ml.preprocessing) -> train/test split -> RandomForestRegressor (compared
against a LinearRegression baseline) -> evaluate (MAE, RMSE, R2) -> save
the better model with joblib -> save a couple of matplotlib plots.

This is intentionally a plain scikit-learn regression problem - no deep
learning, per the project's own "do not overengineer" requirement.
"""

import json

import joblib
import matplotlib
matplotlib.use("Agg")  # no display available on a server
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from config import Config
from database import db
from ml.preprocessing import (
    HOURLY_FEATURE_COLUMNS,
    HOURLY_TARGET_COLUMNS,
    build_hourly_feature_frame,
    hourly_training_rows,
)


def _evaluate(model, X_test, y_test):
    preds = model.predict(X_test)
    return {
        "mae": float(mean_absolute_error(y_test, preds)),
        "rmse": float(np.sqrt(mean_squared_error(y_test, preds))),
        "r2": float(r2_score(y_test, preds)) if len(y_test) > 1 else None,
    }, preds


def train():
    db.init_db()
    with db.get_connection() as conn:
        locations = [dict(row) for row in conn.execute("SELECT id, city, state FROM locations")]
    rows = [
        row for location in locations
        for row in db.get_historical_air_quality_hours(location["id"])
    ]
    frame = build_hourly_feature_frame(rows)
    if frame.empty:
        print("No hourly AQI history is available; skipping training.")
        return

    location_names = {
        row["id"]: f"{row['city']}, {row['state']}"
        for row in locations
    }

    models = {}
    use_model_by_horizon = {}
    location_metrics = {}
    plot_actuals, plot_predictions = [], []
    plot_model = None
    total_training_rows = 0

    for location_id in sorted(frame["location_id"].unique()):
        samples = hourly_training_rows(frame, location_id)
        all_location_rows = frame[frame["location_id"] == location_id]
        last_timestamp = all_location_rows["recorded_at"].max()
        validation_start = last_timestamp - pd.Timedelta(days=14)
        target_end = samples["recorded_at"] + pd.Timedelta(hours=24)
        training = samples[target_end < validation_start]
        validation = samples[
            (samples["recorded_at"] >= validation_start)
            & (target_end <= last_timestamp)
        ]
        if len(training) < 300 or len(validation) < 100:
            print(f"Not enough hourly samples for {location_names.get(location_id, location_id)}; skipping.")
            continue

        X_train = training[HOURLY_FEATURE_COLUMNS].to_numpy(dtype=float)
        y_train = training[HOURLY_TARGET_COLUMNS].to_numpy(dtype=float)
        X_validation = validation[HOURLY_FEATURE_COLUMNS].to_numpy(dtype=float)
        y_validation = validation[HOURLY_TARGET_COLUMNS].to_numpy(dtype=float)

        model = RandomForestRegressor(
            n_estimators=120, max_depth=12, min_samples_leaf=3,
            random_state=42, n_jobs=-1,
        )
        model.fit(X_train, y_train)
        model_metrics, predictions = _evaluate(model, X_validation, y_validation)

        persistence = np.repeat(
            validation["aqi_lag_0"].to_numpy(dtype=float)[:, None],
            len(HOURLY_TARGET_COLUMNS), axis=1,
        )
        persistence_mae = np.mean(np.abs(y_validation - persistence), axis=0)
        model_mae = np.mean(np.abs(y_validation - predictions), axis=0)
        use_horizon = (model_mae < persistence_mae).tolist()
        selected_predictions = np.where(use_horizon, predictions, persistence)

        model.fit(
            samples[HOURLY_FEATURE_COLUMNS].to_numpy(dtype=float),
            samples[HOURLY_TARGET_COLUMNS].to_numpy(dtype=float),
        )
        key = str(location_id)
        models[key] = model
        use_model_by_horizon[key] = use_horizon
        name = location_names.get(location_id, str(location_id))
        location_metrics[name] = {
            "training_origins": int(len(training)),
            "validation_origins": int(len(validation)),
            "model_mae": model_metrics["mae"],
            "model_rmse": model_metrics["rmse"],
            "model_r2": model_metrics["r2"],
            "persistence_mae": float(np.mean(persistence_mae)),
            "selected_model_horizons": int(sum(use_horizon)),
        }
        total_training_rows += len(samples)
        plot_actuals.extend(y_validation[:, 0])
        plot_predictions.extend(selected_predictions[:, 0])
        if plot_model is None:
            plot_model = model
        print(
            f"{name}: hourly MAE {model_metrics['mae']:.2f}, "
            f"persistence MAE {np.mean(persistence_mae):.2f}, "
            f"model selected for {sum(use_horizon)}/24 horizons"
        )

    if not models:
        print("No location had enough hourly data for a validated model.")
        return

    joblib.dump({
        "models": models,
        "use_model_by_horizon": use_model_by_horizon,
        "features": HOURLY_FEATURE_COLUMNS,
        "targets": HOURLY_TARGET_COLUMNS,
        "name": "Hourly RandomForest",
    }, Config.MODEL_PATH)
    with open(Config.METRICS_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "chosen_model": "Hourly RandomForest with persistence selection",
            "training_rows": total_training_rows,
            "hourly_records": len(rows),
            "validation_window_days": 14,
            "locations": location_metrics,
        }, f, indent=2)

    print(f"Saved hourly location models to {Config.MODEL_PATH}")
    _save_plots(np.array(plot_actuals), np.array(plot_predictions), plot_model)


def _save_plots(y_test, preds, rf_model):
    import os
    os.makedirs(Config.PLOTS_DIR, exist_ok=True)

    plt.figure(figsize=(7, 4))
    plt.plot(range(len(y_test)), y_test, label="Actual AQI", marker="o")
    plt.plot(range(len(preds)), preds, label="Predicted AQI", marker="x")
    plt.title("Actual vs Predicted AQI (test set)")
    plt.xlabel("Test sample")
    plt.ylabel("AQI")
    plt.legend()
    plt.tight_layout()
    plt.savefig(f"{Config.PLOTS_DIR}/actual_vs_predicted.png")
    plt.close()

    if hasattr(rf_model, "feature_importances_"):
        plt.figure(figsize=(7, 4))
        order = np.argsort(rf_model.feature_importances_)
        plt.barh(np.array(FEATURE_COLUMNS)[order], rf_model.feature_importances_[order])
        plt.title("Feature importance (RandomForest)")
        plt.tight_layout()
        plt.savefig(f"{Config.PLOTS_DIR}/feature_importance.png")
        plt.close()


if __name__ == "__main__":
    train()
