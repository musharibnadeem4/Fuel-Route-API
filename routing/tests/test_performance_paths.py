import csv
import importlib
import json

import numpy as np
import pytest
from django.core.cache import cache

from routing.models import FuelStation
from routing.services import geocode, trip
from routing.services import stations as stations_service
from routing.services.route_client import RouteResult

URL = "/api/route/"


def test_committed_places_keys_match_the_normalizer():
    """The precomputed key column must be exactly what normalize_city would produce."""
    with geocode.PLACES_CSV.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows and set(rows[0]) == {"city", "state", "lat", "lng", "key"}
    assert all(r["key"] == geocode.normalize_city(r["city"]) for r in rows)


def test_loading_with_and_without_the_key_column_gives_the_same_index(tmp_path):
    rows = [("St. Louis", "MO", 38.63, -90.2), ("DeForest", "WI", 43.25, -89.34), ("Reno", "NV", 39.5, -119.8)]
    with_key = tmp_path / "with_key.csv"
    geocode.write_places_csv(rows, with_key)
    without_key = tmp_path / "without_key.csv"
    without_key.write_text("city,state,lat,lng\n" + "".join(f"{c},{s},{a},{b}\n" for c, s, a, b in rows))

    a, b = geocode.load_index(with_key), geocode.load_index(without_key)
    assert a.cities == b.cities and a.compact == b.compact and a.states == b.states
    assert a.find("Saint Louis", "MO") == (38.63, -90.2)
    assert a.find("De Forest", "WI") == (43.25, -89.34)


def test_index_is_loaded_once_and_can_be_cleared(monkeypatch):
    geocode.clear_index_cache()
    calls = []
    real = geocode.load_index
    monkeypatch.setattr(geocode, "load_index", lambda *a, **k: calls.append(1) or real(*a, **k))
    first, second = geocode.get_index(), geocode.get_index()
    assert first is second and len(calls) == 1
    geocode.clear_index_cache()
    assert geocode.get_index() is not first and len(calls) == 2


def test_route_array_is_built_once_and_matches_coords():
    route = RouteResult([(40.0, -95.0), (41.0, -94.0)], 100.0)
    assert route.array.shape == (2, 2) and route.array.dtype == np.float64
    assert route.array is route.array
    assert route.array.tolist() == [[40.0, -95.0], [41.0, -94.0]]


def test_simplify_route_accepts_lists_and_arrays_identically():
    coords = [(40.0 + i * 0.0001, -100.0 + i * 0.001) for i in range(5000)]
    from_list = trip.simplify_route(coords, max_points=200)
    from_array = trip.simplify_route(np.array(coords), max_points=200)
    assert from_list == from_array and len(from_list) <= 200
    assert from_list[0] == [-100.0, 40.0]


@pytest.mark.django_db
def test_warm_caches_loads_the_place_index_and_station_arrays():
    FuelStation.objects.create(opis_id=1, name="S", address="", city="C", state="NE", price=3.0, lat=40.0, lng=-95.0)
    geocode.clear_index_cache()
    stations_service.invalidate_station_cache()
    assert stations_service._cache is None and geocode._index is None
    trip.warm_caches()
    assert geocode._index is not None
    assert stations_service._cache is not None and len(stations_service._cache) == 1
    stations_service.invalidate_station_cache()


def test_failed_warm_up_does_not_stop_the_app_from_importing(monkeypatch):
    def boom():
        raise RuntimeError("no database yet")

    monkeypatch.setattr(trip, "warm_caches", boom)
    for module in ("config.wsgi", "config.asgi"):
        importlib.reload(importlib.import_module(module))  # must not raise


@pytest.mark.django_db
def test_response_meta_contains_the_timings_breakdown(client, monkeypatch):
    from unittest.mock import Mock

    cache.clear()
    route = RouteResult([(40.0, -100.0 + i * 0.01) for i in range(1001)], 300.0)
    monkeypatch.setattr(trip, "get_route", Mock(return_value=route))
    body = {"start": "Chicago, IL", "finish": "Los Angeles, CA"}

    fresh = client.post(URL, data=json.dumps(body), content_type="application/json").json()["meta"]
    assert set(fresh["timings_ms"]) == {
        "place_lookup", "route_call", "station_match", "planning", "route_simplify", "total_compute"
    }
    assert fresh["timings_ms"]["total_compute"] == fresh["compute_ms"]
    assert all(v >= 0 for v in fresh["timings_ms"].values())

    hit = client.post(URL, data=json.dumps(body), content_type="application/json").json()["meta"]
    assert hit["cached"] is True
    assert hit["timings_ms"]["route_call"] == hit["timings_ms"]["station_match"] == 0.0
    assert hit["timings_ms"]["total_compute"] == hit["compute_ms"]
    cache.clear()
