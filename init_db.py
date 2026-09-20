"""
Vayum - one-time setup script.

    python init_db.py

Creates data/vayum.db with the schema in database/schema.sql, then
geocodes every city in data/locations.json (cached, so re-running this
is instant on the second call).
"""

from database import db
from data_collection.locations import geocode_all

if __name__ == "__main__":
    print("Creating tables...")
    db.init_db()
    print("Geocoding locations (this hits OpenStreetMap once per new city, ~1/sec)...")
    geocode_all()
    print("Done. You can now run: python app.py")
