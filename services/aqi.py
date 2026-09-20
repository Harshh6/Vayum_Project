"""
Vayum - AQI calculation using the CPCB (Central Pollution Control Board)
breakpoint methodology used across India.

Each pollutant concentration is mapped to a 0-500 sub-index using its own
breakpoint table (concentration ranges are NOT linear/comparable across
pollutants, which is why a plain average would be wrong). The overall AQI
is the WORST (maximum) sub-index, and the pollutant that produced it is
reported as the "main pollutant" - exactly how CPCB/CPCB-style AQI works.

Breakpoints below follow the CPCB National Air Quality Index norms.
Units: PM2.5/PM10/NO2/SO2/O3 in µg/m3 (24-hr avg, O3 8-hr), CO in mg/m3 (8-hr).
"""

AQI_BANDS = [
    (0, 50, "Good", "good"),
    (51, 100, "Satisfactory", "satisfactory"),
    (101, 200, "Moderate", "moderate"),
    (201, 300, "Poor", "poor"),
    (301, 400, "Very Poor", "very-poor"),
    (401, 500, "Severe", "severe"),
]

HEALTH_NOTES = {
    "good": "Air quality is clean. A good day to be outdoors.",
    "satisfactory": "Air is acceptable. Very sensitive people may feel minor discomfort.",
    "moderate": "Sensitive groups should limit long outdoor activity.",
    "poor": "Breathing discomfort is likely on prolonged exposure. Reduce outdoor effort.",
    "very-poor": "Avoid outdoor exertion. Children and elderly should stay indoors.",
    "severe": "Serious health risk for everyone. Stay indoors and keep windows shut.",
}

# (BP_lo, BP_hi, I_lo, I_hi) breakpoint tables, CPCB style.
_BREAKPOINTS = {
    "pm25": [(0, 30, 0, 50), (31, 60, 51, 100), (61, 90, 101, 200),
             (91, 120, 201, 300), (121, 250, 301, 400), (251, 500, 401, 500)],
    "pm10": [(0, 50, 0, 50), (51, 100, 51, 100), (101, 250, 101, 200),
             (251, 350, 201, 300), (351, 430, 301, 400), (431, 600, 401, 500)],
    "no2": [(0, 40, 0, 50), (41, 80, 51, 100), (81, 180, 101, 200),
            (181, 280, 201, 300), (281, 400, 301, 400), (401, 600, 401, 500)],
    "so2": [(0, 40, 0, 50), (41, 80, 51, 100), (81, 380, 101, 200),
            (381, 800, 201, 300), (801, 1600, 301, 400), (1601, 2400, 401, 500)],
    "co": [(0.0, 1.0, 0, 50), (1.1, 2.0, 51, 100), (2.1, 10.0, 101, 200),
           (10.1, 17.0, 201, 300), (17.1, 34.0, 301, 400), (34.1, 50.0, 401, 500)],
    "o3": [(0, 50, 0, 50), (51, 100, 51, 100), (101, 168, 101, 200),
           (169, 208, 201, 300), (209, 748, 301, 400), (749, 1000, 401, 500)],
}


def sub_index(pollutant_key, concentration):
    """Map one pollutant's concentration to its 0-500 CPCB sub-index.

    Returns None if the concentration is missing or negative - a missing
    pollutant must never be silently treated as zero.
    """
    if concentration is None or concentration < 0:
        return None

    table = _BREAKPOINTS.get(pollutant_key)
    if table is None:
        return None

    # Above the top of the table -> extrapolate on the final band instead of
    # capping, so a very bad reading still reads as "Severe" rather than 500.
    lo_bp, hi_bp, lo_i, hi_i = table[-1]
    if concentration > hi_bp:
        return round(hi_i + (concentration - hi_bp) * (hi_i - lo_i) / (hi_bp - lo_bp))

    for bp_lo, bp_hi, i_lo, i_hi in table:
        if bp_lo <= concentration <= bp_hi:
            return round(i_lo + (concentration - bp_lo) * (i_hi - i_lo) / (bp_hi - bp_lo))

    return None


def calculate_overall_aqi(pollutant_values):
    """
    pollutant_values: dict like {"pm25": 62, "pm10": 140, "no2": None, ...}
    (a missing/unavailable pollutant should be None, not 0 or omitted).

    Returns (aqi:int|None, category_label, category_key, main_pollutant_key)
    using the CPCB rule: overall AQI = the WORST sub-index among the
    pollutants that actually have data.
    """
    indices = {}
    for key, value in pollutant_values.items():
        idx = sub_index(key, value)
        if idx is not None:
            indices[key] = idx

    if not indices:
        return None, "Unavailable", "unavailable", None

    main_pollutant = max(indices, key=indices.get)
    aqi = min(500, indices[main_pollutant])
    label, key = category_for(aqi)
    return aqi, label, key, main_pollutant


def category_for(aqi):
    for low, high, label, key in AQI_BANDS:
        if low <= aqi <= high:
            return label, key
    return "Severe", "severe"
