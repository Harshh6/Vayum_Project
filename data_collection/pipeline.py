def predict_air_quality(city, state, hours=24):
    """Return the next-hours AQI forecast for the dashboard.

    The hourly forecast path uses Open-Meteo directly when coordinates are
    available, so there is no need to refresh the expensive 90-day history
    on every dashboard request.
    """
    from ml.predict import predict_next_24h

    location_row, lat, lon = _location_row(city, state)

    if not location_row:
        location_row = db.upsert_location(
            city,
            state,
            latitude=lat,
            longitude=lon,
        )

    location_id = location_row["id"]

    # Get the current AQI/pollutants.
    # predict_next_24h can use the hourly Open-Meteo forecast directly.
    current = get_air_quality_data(city, state)

    current_pollutants = {
        p["key"]: p["value"]
        for p in current.get("pollutants", [])
    }

    return predict_next_24h(
        location_id,
        current.get("aqi"),
        current_pollutants,
        latitude=lat,
        longitude=lon,
        hours=hours,
    )