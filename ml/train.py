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
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split

from config import Config
from database import db
from ml.preprocessing import FEATURE_COLUMNS, build_feature_frame, matrix_for_training


def _evaluate(model, X_test, y_test):
    preds = model.predict(X_test)
    return {
        "mae": float(mean_absolute_error(y_test, preds)),
        "rmse": float(np.sqrt(mean_squared_error(y_test, preds))),
        "r2": float(r2_score(y_test, preds)) if len(y_test) > 1 else None,
    }, preds


def train():
    db.init_db()
    rows = db.get_all_historical_rows()
    df = build_feature_frame(rows)
    X, y = matrix_for_training(df)

    if len(X) < 20:
        print(f"Only {len(X)} usable training rows found - run the data "
              f"collection pipeline for a few locations over a few days first "
              f"(see README: 'Data collection'). Skipping training.")
        return

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    rf = RandomForestRegressor(n_estimators=200, max_depth=10, random_state=42)
    rf.fit(X_train, y_train)
    rf_metrics, rf_preds = _evaluate(rf, X_test, y_test)

    lr = LinearRegression()
    lr.fit(X_train, y_train)
    lr_metrics, lr_preds = _evaluate(lr, X_test, y_test)

    print("RandomForestRegressor:", rf_metrics)
    print("LinearRegression:     ", lr_metrics)

    # Prefer the simpler model unless the forest is meaningfully better,
    # per the project's "understandable + working, not maximum complexity" rule.
    use_rf = rf_metrics["mae"] < lr_metrics["mae"] * 0.95
    best_model, best_name, best_metrics, best_preds = (
        (rf, "RandomForestRegressor", rf_metrics, rf_preds) if use_rf
        else (lr, "LinearRegression", lr_metrics, lr_preds)
    )

    joblib.dump({"model": best_model, "features": FEATURE_COLUMNS, "name": best_name}, Config.MODEL_PATH)
    with open(Config.METRICS_PATH, "w", encoding="utf-8") as f:
        json.dump({"chosen_model": best_name, "random_forest": rf_metrics,
                    "linear_regression": lr_metrics, "training_rows": len(X)}, f, indent=2)

    print(f"Saved {best_name} to {Config.MODEL_PATH}")
    _save_plots(y_test, best_preds, rf)


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
