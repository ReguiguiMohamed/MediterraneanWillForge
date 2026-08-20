from data.geo import haversine_km, near_known_city


def test_haversine_matches_known_distance():
    # Athens to Cairo is about 1,120 km.
    assert 1050 < haversine_km(37.98, 23.73, 30.06, 31.24) < 1200


def test_real_metro_stations_are_accepted():
    assert near_known_city(37.943, 23.648, "GR")  # Pireaus, near Athens
    assert near_known_city(41.470, 2.184, "ES")  # Montcada, near Barcelona
    assert near_known_city(39.965, 32.907, "TR")  # Siteler, near Ankara


def test_same_named_cities_elsewhere_are_rejected():
    """The collisions that put a Virginia park in the Egyptian data."""
    assert not near_known_city(38.773, -77.105, "EG")  # Fairfax County, Virginia
    assert not near_known_city(33.918, -83.344, "GR")  # Athens, Georgia
    assert not near_known_city(43.969, 25.330, "EG")  # Alexandria, Romania
    assert not near_known_city(31.946, 35.926, "MA")  # Amman, Jordan
    assert not near_known_city(43.276, 5.397, "MA")  # Marseille Rabatau


def test_countries_without_anchors_are_left_alone():
    """Only WAQI search targets have anchors; others must not be judged."""
    assert near_known_city(52.0, 4.0, "NL")
    assert near_known_city(0.0, 0.0, None)


def test_missing_coordinates_are_rejected_for_anchored_countries():
    assert not near_known_city(None, None, "EG")
    assert not near_known_city("bad", "value", "EG")
