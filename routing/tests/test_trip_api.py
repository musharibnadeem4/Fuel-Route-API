import json
import logging
from unittest.mock import Mock

import numpy as np
import pytest
from django.core.cache import cache
from django.test import Client

from routing.models import FuelStation
from routing.services import stations as stations_service
from routing.services import trip
from routing.services.geocode import resolve_place
from routing.services.route_client import NoRouteFoundError, RouteResult, RouteServiceError

URL = "/api/route/"
MILES_PER_DEG_LAT = 3958.7613 * np.pi / 180


def straight_route(step: float = 0.01, distance_miles: float = 800.0) -> RouteResult:
    """West-to-east along 40N, lng -100..-90, scaled to 800 mi: lng -95 is mile 400, -92.5 is 600."""
    n = round(10 / step) + 1
    return RouteResult([(40.0, -100.0 + i * step) for i in range(n)], distance_miles)


def add_station(opis_id, lng, price, miles_off=2.0):
    return FuelStation.objects.create(
        opis_id=opis_id, name=f"Stop {opis_id}", address="", city="Somewhere", state="NE",
        price=price, lat=40.0 + miles_off / MILES_PER_DEG_LAT, lng=lng,
    )


@pytest.fixture(autouse=True)
def clean_caches():
    cache.clear()
    stations_service.invalidate_station_cache()
    yield
    cache.clear()
    stations_service.invalidate_station_cache()


@pytest.fixture
def get_route(monkeypatch):
    mock = Mock(return_value=straight_route())
    monkeypatch.setattr(trip, "get_route", mock)
    return mock


@pytest.fixture
def worked_example_stations(db):
    # 800 mi trip: A at mile 400 ($3.50), B at mile 600 ($3.00) -> buy 10 gal at A, 20 gal at B.
    add_station(1, -95.0, 3.50)
    add_station(2, -92.5, 3.00)


def post(client, body, **kwargs):
    return client.post(URL, data=json.dumps(body), content_type="application/json", **kwargs)


def assert_error(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code
    assert "Traceback" not in response.content.decode()
    return body["error"]["message"]


def test_happy_path_returns_exact_stops_and_cost(client, get_route, worked_example_stations):
    response = post(client, {"start": "Chicago, IL", "finish": "Los Angeles, CA"})
    assert response.status_code == 200
    body = response.json()

    chicago, la = resolve_place("Chicago, IL"), resolve_place("Los Angeles, CA")
    assert body["start"] == {"name": "Chicago, IL", "lat": pytest.approx(chicago[0]), "lng": pytest.approx(chicago[1])}
    assert body["finish"]["name"] == "Los Angeles, CA"
    get_route.assert_called_once_with(*chicago, *la)

    assert body["distance_miles"] == 800.0
    # Expected (hand computed, same as the planner docstring example):
    #   A: arrive with 10 gal, B is cheaper and 20 gal away -> buy 10 gal * 3.50 = 35.00
    #   B: arrive with 0 gal, destination 200 mi away       -> buy 20 gal * 3.00 = 60.00
    stops = body["fuel_stops"]
    assert [(s["order"], s["name"], s["mile_marker"], s["price_per_gallon"], s["gallons_purchased"], s["cost"]) for s in stops] == [
        (1, "Stop 1", 400.0, 3.5, 10.0, 35.0),
        (2, "Stop 2", 600.0, 3.0, 20.0, 60.0),
    ]
    assert {"city", "state", "lat", "lng"} <= set(stops[0])
    assert body["total_gallons_purchased"] == 30.0
    assert body["total_fuel_cost"] == 95.0

    route = body["route"]
    assert route["type"] == "LineString"
    assert route["coordinates"][0] == [-100.0, 40.0]  # [lng, lat]
    assert route["coordinates"][-1] == [-90.0, 40.0]
    assert body["assumptions"] == {
        "range_miles": 500, "mpg": 10, "start_tank": "full, not charged",
        "corridor_miles": 10, "stop_penalty_usd": 2.0, "price_rule": "lowest price per station",
    }
    meta = body["meta"]
    assert (meta["external_api_calls"], meta["cached"]) == (1, False)
    assert meta["compute_ms"] >= 0 and meta["route_call_ms"] >= 0


def test_repeat_request_is_served_from_cache_without_external_call(client, get_route, worked_example_stations):
    first = post(client, {"start": "Chicago, IL", "finish": "Los Angeles, CA"}).json()
    second = post(client, {"start": "Chicago, IL", "finish": "Los Angeles, CA"}).json()
    assert get_route.call_count == 1
    assert second["meta"]["cached"] is True
    assert second["meta"]["external_api_calls"] == 0
    assert {k: v for k, v in second.items() if k != "meta"} == {k: v for k, v in first.items() if k != "meta"}


def test_case_and_whitespace_variants_share_one_cache_entry(client, get_route, worked_example_stations):
    post(client, {"start": "chicago, il", "finish": "los angeles,CA"})
    second = post(client, {"start": "  Chicago, IL ", "finish": "Los Angeles, CA"})
    assert get_route.call_count == 1
    assert second.json()["meta"]["cached"] is True
    assert second.json()["start"]["name"] == "Chicago, IL"  # canonical name, not the raw text


def test_changing_the_stop_penalty_does_not_serve_a_stale_cached_plan(client, get_route, worked_example_stations, settings):
    post(client, {"start": "Chicago, IL", "finish": "Los Angeles, CA"})
    settings.FUEL_STOP_PENALTY = 5.0
    body = post(client, {"start": "Chicago, IL", "finish": "Los Angeles, CA"}).json()
    assert get_route.call_count == 2
    assert body["meta"]["cached"] is False
    assert body["assumptions"]["stop_penalty_usd"] == 5.0


def test_reversed_trip_is_a_different_cache_entry(client, get_route, worked_example_stations):
    post(client, {"start": "Chicago, IL", "finish": "Los Angeles, CA"})
    post(client, {"start": "Los Angeles, CA", "finish": "Chicago, IL"})
    assert get_route.call_count == 2


def test_lat_lng_input(client, get_route, worked_example_stations):
    response = post(client, {"start": "41.8781,-87.6298", "finish": " 39.7392 , -104.9903"})
    assert response.status_code == 200
    body = response.json()
    assert body["start"] == {"name": "41.8781, -87.6298", "lat": 41.8781, "lng": -87.6298}
    assert body["finish"]["lat"] == 39.7392
    get_route.assert_called_once_with(41.8781, -87.6298, 39.7392, -104.9903)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"start": "Chicago, IL"},
        {"start": "", "finish": "Reno, NV"},
        {"start": "   ", "finish": "Reno, NV"},
        {"start": ["Chicago, IL"], "finish": "Reno, NV"},
        {"start": "x" * 101, "finish": "Reno, NV"},
    ],
)
def test_invalid_input_is_400(client, get_route, body):
    message = assert_error(post(client, body), 400, "invalid_input")
    assert "start" in message or "finish" in message
    get_route.assert_not_called()


@pytest.mark.parametrize(
    "start, finish, fragment",
    [
        ("Atlantis, NV", "Reno, NV", "Unknown US place"),
        ("Paris, France", "Reno, NV", "Could not parse"),
        ("Chicago", "Reno, NV", "Could not parse"),
        ("95.0,-87.0", "Reno, NV", "out of range"),
        ("Chicago, IL", "Toronto, ON", "Could not parse"),
    ],
)
def test_unknown_or_non_us_place_is_400(client, get_route, start, finish, fragment):
    message = assert_error(post(client, {"start": start, "finish": finish}), 400, "invalid_place")
    assert fragment in message
    get_route.assert_not_called()


def test_start_equal_to_finish_is_400(client, get_route):
    chicago_as_lat_lng = "{}, {}".format(*resolve_place("Chicago, IL"))
    for finish in ("chicago,  il", "Chicago, IL", chicago_as_lat_lng):
        message = assert_error(post(client, {"start": "Chicago, IL", "finish": finish}), 400, "same_location")
        assert "same place" in message
    get_route.assert_not_called()


def test_gap_longer_than_range_is_422_with_location(client, get_route, db):
    # No stations near the route: the 800 mi trip has a 800 mi gap.
    message = assert_error(post(client, {"start": "Chicago, IL", "finish": "Los Angeles, CA"}), 422, "no_feasible_route")
    assert "mile" in message and "gap" in message


def test_route_not_found_is_404(client, get_route):
    get_route.side_effect = NoRouteFoundError("No drivable route found between those locations.", 404)
    message = assert_error(post(client, {"start": "Chicago, IL", "finish": "Los Angeles, CA"}), 404, "no_route_found")
    assert "drivable" in message


def test_routing_service_failure_is_502_and_is_not_cached(client, get_route, worked_example_stations):
    get_route.side_effect = RouteServiceError("Routing service unavailable (HTTP 503).", 503)
    assert_error(post(client, {"start": "Chicago, IL", "finish": "Los Angeles, CA"}), 502, "routing_service_error")

    get_route.side_effect = None
    get_route.return_value = straight_route()
    assert post(client, {"start": "Chicago, IL", "finish": "Los Angeles, CA"}).status_code == 200
    assert get_route.call_count == 2  # the failure did not poison the cache


def test_unexpected_exception_is_500_json_without_traceback(client, get_route):
    get_route.side_effect = RuntimeError("secret internals")
    message = assert_error(post(client, {"start": "Chicago, IL", "finish": "Los Angeles, CA"}), 500, "internal_error")
    assert "secret internals" not in message


def test_malformed_json_and_wrong_method_use_the_error_shape(client):
    bad = client.post(URL, data="{not json", content_type="application/json")
    assert_error(bad, 400, "invalid_input")
    assert_error(client.get(URL), 405, "method_not_allowed")
    form = client.post(URL, data={"start": "a"})  # multipart is not accepted
    assert_error(form, 415, "unsupported_media_type")


def test_no_csrf_or_session_needed():
    strict_client = Client(enforce_csrf_checks=True)
    response = strict_client.post(URL, data=json.dumps({"start": "Chicago, IL"}), content_type="application/json")
    assert_error(response, 400, "invalid_input")  # reached validation, not a 403 CSRF failure


def test_route_geometry_is_limited_and_keeps_endpoints(client, get_route, worked_example_stations):
    get_route.return_value = straight_route(step=0.002)  # 5,001 vertices
    coordinates = post(client, {"start": "Chicago, IL", "finish": "Los Angeles, CA"}).json()["route"]["coordinates"]
    assert len(coordinates) <= trip.ROUTE_POINT_LIMIT
    assert coordinates[0] == [-100.0, 40.0] and coordinates[-1] == [-90.0, 40.0]


def test_simplify_route_keeps_short_routes_and_shape():
    short = [(40.0, -100.0 + i * 0.1) for i in range(50)]
    assert len(trip.simplify_route(short)) == 50
    # A right-angle corner must survive simplification of a dense L-shaped path.
    path = [(40.0, -100.0 + i * 0.001) for i in range(3000)] + [(40.0 + i * 0.001, -97.001) for i in range(1, 3000)]
    simplified = trip.simplify_route(path, max_points=100)
    assert len(simplified) <= 100
    assert simplified[0] == [-100.0, 40.0] and simplified[-1] == [-97.001, 42.999]
    assert [-97.001, 40.0] in simplified or any(abs(p[0] + 97.001) < 0.01 and abs(p[1] - 40.0) < 0.01 for p in simplified)


def test_timing_is_logged_without_the_api_key(client, get_route, worked_example_stations, settings, caplog):
    settings.ORS_API_KEY = "SECRET-KEY-123"
    with caplog.at_level(logging.INFO, logger="routing"):
        post(client, {"start": "Chicago, IL", "finish": "Los Angeles, CA"})
    assert "route_call_ms=" in caplog.text and "matching_ms=" in caplog.text and "planning_ms=" in caplog.text
    assert "SECRET-KEY-123" not in caplog.text


def test_plan_trip_is_usable_without_http(get_route, worked_example_stations):
    result = trip.plan_trip("Chicago, IL", "Los Angeles, CA")
    assert result["total_fuel_cost"] == 95.0
