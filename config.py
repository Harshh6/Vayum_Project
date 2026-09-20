"""
Vayum - central configuration.

Everything environment-specific (API keys, cache lifetimes, file paths)
lives here so the rest of the codebase never hardcodes it.
"""

import os

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "vayum-college-project-secret-key")

    # --- API keys (never hardcoded, always from the environment) ----------
    WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "")
    CPCB_API_KEY = os.environ.get("CPCB_API_KEY", "")
    OPENAQ_API_KEY = os.environ.get("OPENAQ_API_KEY", "")

    # --- paths --------------------------------------------------------------
    DATA_DIR = os.path.join(BASE_DIR, "data")
    LOCATIONS_FILE = os.path.join(DATA_DIR, "locations.json")
    DB_PATH = os.path.join(DATA_DIR, "vayum.db")
    SCHEMA_PATH = os.path.join(BASE_DIR, "database", "schema.sql")
    MODELS_DIR = os.path.join(BASE_DIR, "models")
    MODEL_PATH = os.path.join(MODELS_DIR, "aqi_model.joblib")
    METRICS_PATH = os.path.join(MODELS_DIR, "metrics.json")
    PLOTS_DIR = os.path.join(BASE_DIR, "ml", "plots")

    # --- caching (avoid hammering free-tier APIs) ---------------------------
    AIR_QUALITY_CACHE_MINUTES = _int_env("AIR_QUALITY_CACHE_MINUTES", 60)
    WEATHER_CACHE_MINUTES = _int_env("WEATHER_CACHE_MINUTES", 120)

    # --- external endpoints ---------------------------------------------
    OPENAQ_BASE_URL = "https://api.openaq.org/v3"
    CPCB_API_URL = "https://api.data.gov.in/resource/3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69"
    WEATHERAPI_BASE_URL = "https://api.weatherapi.com/v1"
    OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
    OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
    OPEN_METEO_AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
    NOMINATIM_USER_AGENT = "vayum-college-project"

    REQUEST_TIMEOUT = 10  # seconds, for every outbound API call


os.makedirs(Config.MODELS_DIR, exist_ok=True)
