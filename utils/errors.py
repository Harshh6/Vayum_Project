"""Vayum - shared error types. Keeps raw stack traces away from the user."""


class VayumDataError(Exception):
    """Raised when a data source is unavailable/invalid for a location.
    Routes catch this and show a clean message instead of a 500 page."""


def error_response(message):
    return {"error": True, "message": message}
