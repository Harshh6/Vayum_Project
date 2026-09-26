"""
Vayum - feature engineering for the AQI prediction model.

Turns the raw `historical_data` rows (one row per location per day) into a
feature matrix suitable for scikit-learn:

  - calendar features: day of week, day of month, month
  - lag features: aqi/pm25 from the previous 1, 2 and 3 days
  - rolling features: 3-day rolling mean of aqi
  - weather features used as-is: temp, humidity, wind, pressure, precip

Target: next day's AQI (aqi shifted by -1 within each location).
"""

import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    "pm25", "pm10", "no2", "so2", "co", "o3",
    "temp_c", "humidity", "wind_kph", "pressure_mb", "precip_mm",
    "day_of_week", "day_of_month", "month",
    "aqi_lag1", "aqi_lag2", "aqi_lag3", "aqi_roll3",
]
TARGET_COLUMN = "aqi_next"
HOURLY_AQI_LAGS = (0, 1, 3, 6, 12, 24, 48, 168)
HOURLY_AQI_WINDOWS = (3, 6, 24, 168)
HOURLY_POLLUTANTS = ("pm25", "pm10", "no2", "so2", "co", "o3")
HOURLY_FEATURE_COLUMNS = [
    "hour_sin", "hour_cos", "weekday_sin", "weekday_cos", "year_day_sin", "year_day_cos",
] + [f"aqi_lag_{lag}" for lag in HOURLY_AQI_LAGS] + [
    f"aqi_mean_{window}" for window in HOURLY_AQI_WINDOWS
] + list(HOURLY_POLLUTANTS)
HOURLY_TARGET_COLUMNS = [f"aqi_t_plus_{hour}" for hour in range(1, 25)]


def build_feature_frame(rows):
    """rows: list of dicts from database.db.get_all_historical_rows()
    (or a single location's get_historical_rows()).
    Returns a cleaned pandas DataFrame with FEATURE_COLUMNS + TARGET_COLUMN
    (target is NaN for the most recent day of each location, since there is
    no "next day" for it yet - drop those rows when training, keep the row
    itself when building features for a live prediction)."""
    if not rows:
        return pd.DataFrame(columns=["location_id", "date"] + FEATURE_COLUMNS + [TARGET_COLUMN])

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df.sort_values(["location_id", "date"], inplace=True)
    df.drop_duplicates(subset=["location_id", "date"], keep="last", inplace=True)

    df["day_of_week"] = df["date"].dt.dayofweek
    df["day_of_month"] = df["date"].dt.day
    df["month"] = df["date"].dt.month

    grouped = df.groupby("location_id")["aqi"]
    df["aqi_lag1"] = grouped.shift(1)
    df["aqi_lag2"] = grouped.shift(2)
    df["aqi_lag3"] = grouped.shift(3)
    df["aqi_roll3"] = grouped.transform(lambda s: s.shift(1).rolling(3, min_periods=1).mean())
    df[TARGET_COLUMN] = grouped.shift(-1)

    # Fill small pollutant/weather gaps per-location; never invent an AQI value.
    fill_cols = ["pm25", "pm10", "no2", "so2", "co", "o3",
                 "temp_c", "humidity", "wind_kph", "pressure_mb", "precip_mm"]
    df[fill_cols] = df.groupby("location_id")[fill_cols].transform(
        lambda s: s.interpolate(limit=2).ffill().bfill()
    )

    return df


def matrix_for_training(df):
    """Drop rows without a target or without enough lag history, return (X, y)."""
    usable = df.dropna(subset=[TARGET_COLUMN, "aqi_lag1"]).copy()
    usable[FEATURE_COLUMNS] = usable[FEATURE_COLUMNS].fillna(usable[FEATURE_COLUMNS].median(numeric_only=True))
    X = usable[FEATURE_COLUMNS].to_numpy(dtype=float)
    y = usable[TARGET_COLUMN].to_numpy(dtype=float)
    return X, y


def latest_feature_row(df, location_id):
    """The single most recent row for one location, ready to feed model.predict()."""
    subset = df[df["location_id"] == location_id].sort_values("date")
    if subset.empty:
        return None
    row = subset.iloc[[-1]].copy()
    row[FEATURE_COLUMNS] = row[FEATURE_COLUMNS].fillna(0)
    return row[FEATURE_COLUMNS].to_numpy(dtype=float)


def build_hourly_feature_frame(rows):
    """Build features at each hour and direct targets for the next 24 hours."""
    columns = ["location_id", "recorded_at"] + HOURLY_FEATURE_COLUMNS + HOURLY_TARGET_COLUMNS
    if not rows:
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(rows)
    df["recorded_at"] = pd.to_datetime(df["recorded_at"])
    df.sort_values(["location_id", "recorded_at"], inplace=True)
    df.drop_duplicates(subset=["location_id", "recorded_at"], keep="last", inplace=True)

    hour = df["recorded_at"].dt.hour + df["recorded_at"].dt.minute / 60
    weekday = df["recorded_at"].dt.dayofweek
    year_day = df["recorded_at"].dt.dayofyear
    df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    df["weekday_sin"] = np.sin(2 * np.pi * weekday / 7)
    df["weekday_cos"] = np.cos(2 * np.pi * weekday / 7)
    df["year_day_sin"] = np.sin(2 * np.pi * year_day / 365.25)
    df["year_day_cos"] = np.cos(2 * np.pi * year_day / 365.25)

    grouped = df.groupby("location_id", sort=False)["aqi"]
    for lag in HOURLY_AQI_LAGS:
        df[f"aqi_lag_{lag}"] = grouped.shift(lag)
    for window in HOURLY_AQI_WINDOWS:
        df[f"aqi_mean_{window}"] = grouped.transform(
            lambda values: values.rolling(window, min_periods=window).mean()
        )
    for hour in range(1, 25):
        df[f"aqi_t_plus_{hour}"] = grouped.shift(-hour)

    return df


def hourly_training_rows(frame, location_id):
    subset = frame[frame["location_id"] == location_id].copy()
    return subset.dropna(subset=HOURLY_FEATURE_COLUMNS + HOURLY_TARGET_COLUMNS)


def latest_hourly_feature_row(rows, location_id):
    frame = build_hourly_feature_frame(rows)
    subset = frame[frame["location_id"] == location_id].sort_values("recorded_at")
    subset = subset.dropna(subset=HOURLY_FEATURE_COLUMNS)
    if subset.empty:
        return None
    return subset.iloc[[-1]][HOURLY_FEATURE_COLUMNS].to_numpy(dtype=float)
