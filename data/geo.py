"""
data/geo.py
───────────
City anchors for the WAQI searches, and the distance check that keeps a
name collision out of the lake.

WAQI is searched by city keyword, and city names are not unique on Earth.
A search for Alexandria returns Alexandria in Virginia and Alexandria in
Romania alongside the Egyptian one; Athens returns Athens, Georgia; and
Rabat substring-matches "Marseille Rabatau". The ingestor used to stamp
every result with the country it had asked about, so a park in Fairfax
County was filed under Egypt and scored in the wildfire risk index.

A returned station is accepted only if it sits within MAX_KM of one of the
anchor cities for the country being queried. Real metro stations land
within about 15 km of their anchor; every observed collision was over
1,400 km away, so the threshold is not delicate.
"""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

# Anchor coordinates per country, one per curated WAQI city search.
CITY_ANCHORS: dict[str, tuple[tuple[float, float], ...]] = {
    "LB": ((33.89, 35.50),),  # Beirut
    "MA": (
        (33.57, -7.59),
        (34.02, -6.84),
        (31.63, -8.01),
    ),  # Casablanca, Rabat, Marrakech
    "TN": ((36.82, 10.17),),  # Tunis
    "DZ": ((36.74, 3.06),),  # Algiers
    "EG": ((30.06, 31.24), (31.20, 29.92)),  # Cairo, Alexandria
    "TR": ((41.01, 28.95), (39.93, 32.86)),  # Istanbul, Ankara
    "GR": ((37.98, 23.73),),  # Athens
    "ES": ((40.42, -3.70), (41.39, 2.17)),  # Madrid, Barcelona
    "IT": ((41.90, 12.50), (45.46, 9.19)),  # Rome, Milan
}

# Generous enough for a metro area, far tighter than any name collision seen.
MAX_KM = 100.0

_EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * asin(sqrt(a))


def near_known_city(
    lat: float | None,
    lon: float | None,
    country_code: str | None,
    max_km: float = MAX_KM,
) -> bool:
    """True if the point is within max_km of an anchor city for that country.

    A country with no anchors is not a WAQI search target, so its rows are
    left alone rather than judged against a list that does not describe them.
    """
    anchors = CITY_ANCHORS.get((country_code or "").upper())
    if not anchors:
        return True
    if lat is None or lon is None:
        return False
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    return any(
        haversine_km(lat_f, lon_f, a_lat, a_lon) <= max_km for a_lat, a_lon in anchors
    )
