"""
Vayum - Air quality monitoring and forecasting platform for India.

This file is the Flask foundation of the project. The real data-science
work (API calls, pandas/numpy cleaning, the AQI formula, the scikit-learn
model) lives in data_collection/, services/ and ml/ - this file only wires
routes to that layer and renders templates. The templates and static files
are untouched from the original UI.
"""

import json
import os

from flask import (Flask, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)

from config import Config
from database import db
from data_collection.pipeline import (get_air_quality_data, get_history_data,
                                       get_weather_data, predict_air_quality)
from utils.errors import VayumDataError

app = Flask(__name__)
app.secret_key = Config.SECRET_KEY

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCATIONS_FILE = Config.LOCATIONS_FILE

db.init_db()  # safe to call every boot - only creates tables if missing


# ---------------------------------------------------------------------------
# LOCATIONS  (dropdown data only - lat/lon lives in the database, see
# data_collection/locations.py)
# ---------------------------------------------------------------------------

def load_locations():
    """Read data/locations.json and return it as a dict {state: [cities]}."""
    with open(LOCATIONS_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {s["name"]: sorted(s["cities"]) for s in raw["states"]}


LOCATIONS = load_locations()
STATES = sorted(LOCATIONS.keys())


def _selected_location():
    city, state = session.get("city"), session.get("state")
    if state not in LOCATIONS or city not in LOCATIONS[state]:
        session.pop("city", None)
        session.pop("state", None)
        return None, None
    return city, state


# ---------------------------------------------------------------------------
# Safe wrappers - never let a data-source failure leak a stack trace to the
# user; fall back to a clearly-labelled "unavailable" payload instead.
# ---------------------------------------------------------------------------

def _safe_air_quality(city, state):
    try:
        return get_air_quality_data(city, state)
    except Exception:
        return {
            "aqi": None, "label": "Unavailable", "level": "unavailable",
            "health_note": "Air-quality data is currently unavailable for this location.",
            "main_pollutant": "N/A", "pollutants": [], "updated": "—",
        }


def _safe_history(city, state):
    try:
        return get_history_data(city, state)
    except Exception:
        return {"labels": [], "values": []}


def _safe_prediction(city, state):
    try:
        return predict_air_quality(city, state)
    except Exception:
        return {"labels": [], "values": [], "model": "Unavailable", "pollutants": []}


def _safe_weather(city, state):
    try:
        return get_weather_data(city, state)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Location selection screen."""
    return render_template("index.html", states=STATES)


@app.route("/location-logo")
def location_logo():
    """Serve the landing-page background logo kept at the project root."""
    return send_from_directory(BASE_DIR, "50%-transparent-logo.png")


@app.route("/vayum-logo")
def vayum_logo():
    """Serve the navbar logo kept at the project root."""
    return send_from_directory(BASE_DIR, "vayum-logoo.png")


@app.route("/select-location", methods=["POST"])
def select_location():
    state = request.form.get("state", "").strip()
    city = request.form.get("city", "").strip()
    if state in LOCATIONS and city in LOCATIONS[state]:
        session["state"] = state
        session["city"] = city
        return redirect(url_for("dashboard"))
    return redirect(url_for("index"))


@app.route("/dashboard")
def dashboard():
    city, state = _selected_location()
    if not city or not state:
        return redirect(url_for("index"))
    return render_template(
        "dashboard.html",
        city=city,
        state=state,
        states=STATES,
        air=_safe_air_quality(city, state),
        history=_safe_history(city, state),
        prediction=_safe_prediction(city, state),
        weather=_safe_weather(city, state),
    )


@app.route("/prediction")
def prediction_page():
    city, state = _selected_location()
    if not city or not state:
        return redirect(url_for("index"))
    return render_template(
        "prediction.html",
        city=city,
        state=state,
        states=STATES,
        prediction=_safe_prediction(city, state),
    )


@app.route("/history")
def history_page():
    city, state = _selected_location()
    if not city or not state:
        return redirect(url_for("index"))
    history = _safe_history(city, state)
    return render_template(
        "history.html",
        city=city,
        state=state,
        states=STATES,
        history=history,
        history_rows=list(zip(history["labels"], history["values"])),
    )


@app.route("/about")
def about():
    city, state = _selected_location()
    return render_template("about.html", city=city, state=state, states=STATES)


@app.route("/help")
def help_page():
    city, state = _selected_location()
    return render_template("help.html", city=city, state=state, states=STATES)


@app.route("/change-location")
def change_location():
    session.pop("city", None)
    session.pop("state", None)
    return redirect(url_for("index"))


# --- small JSON endpoints used by the frontend ------------------------------

@app.route("/api/cities/<state>")
def api_cities(state):
    """Cities of a state, alphabetically. Used by the dependent dropdown."""
    return jsonify(LOCATIONS.get(state, []))


@app.route("/api/air-quality")
def api_air_quality():
    city, state = _selected_location()
    if not city or not state:
        return jsonify({"error": True, "message": "No location selected"}), 400
    try:
        return jsonify(get_air_quality_data(city, state))
    except VayumDataError as exc:
        return jsonify({"error": True, "message": str(exc)}), 502


if __name__ == "__main__":
    app.run(debug=True)
