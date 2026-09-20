"""
Vayum - location geocoding.

The UI's data/locations.json only has state -> [city names]. Air-quality
and weather APIs need latitude/longitude, so this module resolves each
city once (via OpenStreetMap/Nominatim, no key required) and caches the
result in the `locations` table. After the first run, get_coordinates()
is just a DB read - we never geocode the same city twice.
"""

import json
import time

from geopy.geocoders import Nominatim
from geopy.exc import GeopyError

from config import Config
from database import db

_geolocator = Nominatim(user_agent=Config.NOMINATIM_USER_AGENT, timeout=Config.REQUEST_TIMEOUT)


def _geocode(city, state):
    """One Nominatim lookup. Returns (lat, lon) or (None, None)."""
    for query in (f"{city}, {state}, India", f"{city}, India"):
        try:
            location = _geolocator.geocode(query)
        except GeopyError:
            location = None
        if location:
            return location.latitude, location.longitude
        time.sleep(1)  # be polite to the free Nominatim service
    return None, None


def get_coordinates(city, state):
    """Return (lat, lon) for a city, geocoding + caching it if this is the
    first time we've seen it."""
    row = db.get_location(city, state)
    if row and row.get("latitude") is not None:
        return row["latitude"], row["longitude"]

    lat, lon = _geocode(city, state)
    db.upsert_location(city, state, latitude=lat, longitude=lon)
    return lat, lon


def geocode_all(locations_file=None):
    """One-off setup script: geocode every city in locations.json and store
    it, so the running app never needs to call Nominatim on a page load.
    Run with: python -m data_collection.locations
    """
    with open(locations_file or Config.LOCATIONS_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)

    total = sum(len(s["cities"]) for s in raw["states"])
    done = 0
    for state_entry in raw["states"]:
        state = state_entry["name"]
        for city in state_entry["cities"]:
            done += 1
            existing = db.get_location(city, state)
            if existing and existing.get("latitude") is not None:
                print(f"[{done}/{total}] {city}, {state} - already cached")
                continue
            lat, lon = _geocode(city, state)
            db.upsert_location(city, state, latitude=lat, longitude=lon)
            status = f"{lat:.3f},{lon:.3f}" if lat is not None else "NOT FOUND"
            print(f"[{done}/{total}] {city}, {state} -> {status}")
            time.sleep(1)  # Nominatim usage policy: max ~1 request/second


if __name__ == "__main__":
    db.init_db()
    geocode_all()
