import time

import numpy as np
import pytest

from routing.models import FuelStation
from routing.services import stations as st
from routing.services.route_client import RouteResult

MILES_PER_DEG_LAT = 3958.7613 * np.pi / 180  # ~69.09


def lat_offset(miles: float) -> float:
    """Latitude that is `miles` north of the 40N test route."""
    return 40.0 + miles / MILES_PER_DEG_LAT


def straight_route(step: float = 0.01, distance_miles: float = 600.0) -> RouteResult:
    """West-to-east along 40N from -100 to -90; vertex i is at lng -100 + i*step."""
    n = round(10 / step) + 1
    return RouteResult([(40.0, -100.0 + i * step) for i in range(n)], distance_miles)


def make_station(opis_id, lat, lng, price=3.0, **extra):
    return FuelStation.objects.create(
        opis_id=opis_id, name=f"S{opis_id}", address="", city="C", state="NE",
        price=price, lat=lat, lng=lng, **extra,
    )


@pytest.fixture(autouse=True)
def fresh_cache():
    st.invalidate_station_cache()
    yield
    st.invalidate_station_cache()


@pytest.mark.django_db
def test_corridor_membership_and_mile_markers_in_order():
    # Route is scaled to 600 mi, so lng -95 (half way) is mile 300 and lng -97 is mile 180.
    make_station(1, lat_offset(5), -95.0)   # 5 mi off the route   -> in,  mile 300
    make_station(2, lat_offset(9), -97.0)   # 9 mi off             -> in,  mile 180
    make_station(3, lat_offset(11), -96.0)  # 11 mi off            -> out
    make_station(4, 41.0, -95.0)            # ~69 mi off           -> out
    make_station(5, 40.0, -100.0)           # exactly at the start -> in,  mile 0
    make_station(6, 40.0, -90.0)            # exactly at the end   -> in,  mile 600
    make_station(7, lat_offset(6), -92.5)   # 6 mi off             -> in,  mile 450
    result = st.candidates_along_route(straight_route(), corridor_miles=10)

    assert [c.station_id for c in result] == [5, 2, 1, 7, 6]
    assert [c.mile_marker for c in result] == pytest.approx([0, 180, 300, 450, 600], abs=1e-6)
    first = result[0]
    assert (first.name, first.city, first.state, first.price) == ("S5", "C", "NE", 3.0)


@pytest.mark.django_db
def test_station_just_before_start_snaps_to_mile_zero_and_far_one_is_dropped():
    make_station(1, 40.0, -100.1)  # ~5.3 mi west of the start -> in, mile 0
    make_station(2, 40.0, -100.3)  # ~15.9 mi west             -> out
    result = st.candidates_along_route(straight_route(), corridor_miles=10)
    assert [(c.station_id, c.mile_marker) for c in result] == [(1, 0.0)]


@pytest.mark.django_db
def test_default_corridor_comes_from_settings(settings):
    make_station(1, lat_offset(7), -95.0)
    settings.STATION_CORRIDOR_MILES = 10
    assert len(st.candidates_along_route(straight_route())) == 1
    settings.STATION_CORRIDOR_MILES = 5
    assert st.candidates_along_route(straight_route()) == []


@pytest.mark.django_db
def test_stations_without_exact_coordinates_are_excluded():
    make_station(1, lat_offset(2), -95.0)
    make_station(2, lat_offset(2), -95.0, geo_approx=True)
    FuelStation.objects.create(opis_id=3, name="x", address="", city="C", state="NE", price=3.0)
    assert [c.station_id for c in st.candidates_along_route(straight_route())] == [1]


@pytest.mark.django_db
def test_station_arrays_are_cached_until_invalidated(django_assert_num_queries):
    make_station(1, lat_offset(2), -95.0)
    route = straight_route()
    assert len(st.candidates_along_route(route)) == 1  # loads the cache
    make_station(2, lat_offset(2), -96.0)
    with django_assert_num_queries(0):
        assert len(st.candidates_along_route(route)) == 1  # served from memory; new row unseen
    st.invalidate_station_cache()
    assert len(st.candidates_along_route(route)) == 2


@pytest.mark.django_db
def test_no_stations_returns_empty_list():
    assert st.candidates_along_route(straight_route()) == []


def test_dense_route_is_downsampled_but_keeps_ends_and_distances():
    route = straight_route(step=0.001)  # 10,001 vertices
    xyz, markers = st.route_points(route)
    assert len(markers) <= st.MAX_ROUTE_VERTICES and xyz.shape == (len(markers), 3)
    assert markers[0] == 0.0 and markers[-1] == pytest.approx(600.0)
    arrays = st.build_station_arrays([(1, "S", "C", "NE", 3.0, lat_offset(5), -95.0)])
    (hit,) = st.find_candidates(route, arrays, 10)
    assert hit.mile_marker == pytest.approx(300.0, abs=0.2)


def test_short_route_is_not_downsampled():
    _, markers = st.route_points(straight_route())
    assert len(markers) == 1001


def _us_stations(n: int, seed: int = 7) -> st.StationArrays:
    rng = np.random.default_rng(seed)
    lat, lng = rng.uniform(25, 49, n), rng.uniform(-124, -67, n)
    rows = [(i, f"S{i}", "C", "TX", 3.0 + (i % 100) / 100, lat[i], lng[i]) for i in range(n)]
    return st.build_station_arrays(rows)


def test_benchmark_3000_vertex_route_against_6626_stations(capsys):
    arrays = _us_stations(6626)
    t = np.linspace(0, 1, 3000)
    coords = list(zip(34.05 + t * (40.71 - 34.05), -118.24 + t * (-74.0 + 118.24)))  # LA -> NYC
    route = RouteResult(coords, 2800.0)
    st.find_candidates(route, arrays, 10)  # warm-up
    best = float("inf")
    for _ in range(5):
        start = time.perf_counter()
        result = st.find_candidates(route, arrays, 10)
        best = min(best, time.perf_counter() - start)
    with capsys.disabled():
        print(f"\n[benchmark] 3000 vertices x 6626 stations: {best * 1000:.1f} ms, {len(result)} candidates")
    assert result and [c.mile_marker for c in result] == sorted(c.mile_marker for c in result)
    assert best < 0.25  # target is < 50 ms; the bound is loose so slow machines do not flake
