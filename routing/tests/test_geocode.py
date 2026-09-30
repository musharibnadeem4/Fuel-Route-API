import pytest

from routing.services.geocode import (
    PlaceNotFoundError,
    build_places,
    city_keys,
    index_from_rows,
    normalize_city,
    resolve_place,
)

ROWS = [
    {"city": "Saint Louis", "state": "MO", "lat": "38.63", "lng": "-90.2"},
    {"city": "Fort Wayne", "state": "IN", "lat": "41.08", "lng": "-85.14"},
    {"city": "DeForest", "state": "WI", "lat": "43.25", "lng": "-89.34"},
    {"city": "Reno", "state": "NV", "lat": "39.5", "lng": "-119.8"},
    {"city": "Las Vegas", "state": "NV", "lat": "36.17", "lng": "-115.14"},
]


@pytest.fixture
def index():
    return index_from_rows(ROWS)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("St. Louis", "saint louis"),
        ("Ft. Wayne", "fort wayne"),
        ("Mt. Pleasant", "mount pleasant"),
        ("Port St. Lucie", "port saint lucie"),
        ("O'Fallon", "ofallon"),
        ("  WINSTON-SALEM  ", "winston salem"),
        ("Smith (Township)", "smith"),
        ("Cañon City", "canon city"),
    ],
)
def test_normalize_city(raw, expected):
    assert normalize_city(raw) == expected


def test_city_keys_strip_township_suffix():
    assert city_keys("Union Township") == ["union township", "union"]
    assert city_keys("Reno") == ["reno"]


def test_build_places_averages_zip_centroids_per_city():
    lines = [
        "US\t1\tReno\tNevada\tNV\tWashoe\t031\t\t\t39.0\t-119.0\t4",
        "US\t2\tReno\tNevada\tNV\tWashoe\t031\t\t\t40.0\t-120.0\t4",
        "US\t3\tToronto\tOntario\tON\t\t\t\t\t43.0\t-79.0\t4",
    ]
    assert build_places(lines) == [("Reno", "NV", 39.5, -119.5)]


def test_find_uses_normalization_and_compact_key(index):
    assert index.find("St. Louis", "mo") == (38.63, -90.2)
    assert index.find("Ft Wayne", "IN") == (41.08, -85.14)
    assert index.find("De Forest", "WI") == (43.25, -89.34)
    assert index.find("Nowhere", "NV") is None


def test_state_centroid_is_mean_of_cities(index):
    lat, lng = index.state_centroid("NV")
    assert (round(lat, 3), round(lng, 3)) == (37.835, -117.47)
    assert index.state_centroid("ZZ") is None


def test_resolve_city_state(index):
    assert resolve_place("Reno, NV", index) == (39.5, -119.8)
    assert resolve_place("  saint louis ,mo ", index) == (38.63, -90.2)


def test_resolve_lat_lng_needs_no_index():
    assert resolve_place("40.7128, -74.006") == (40.7128, -74.006)
    assert resolve_place("-33.5,151") == (-33.5, 151.0)


@pytest.mark.parametrize("text", ["", "Reno", "Reno, Nevada", "Reno, XX", "95,10", "10,200"])
def test_resolve_rejects_unparseable_input(text, index):
    with pytest.raises(PlaceNotFoundError):
        resolve_place(text, index)


def test_resolve_unknown_place_message(index):
    with pytest.raises(PlaceNotFoundError, match="Unknown US place: 'Atlantis', NV"):
        resolve_place("Atlantis, NV", index)
