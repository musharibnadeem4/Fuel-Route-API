"""Regression lock: optimizations must not change results.

Golden values were captured from the pre-optimization code on the real Chicago->LA and
NYC->Dallas ORS routes (routing/tests/fixtures/*_route.json) and on seeded random
planner cases. Regenerate deliberately with:  UPDATE_GOLDEN=1 pytest routing/tests/test_regression_golden.py
"""
import hashlib
import io
import json
import os
import random
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.management import call_command

from routing.models import FuelStation
from routing.services import stations as stations_service
from routing.services import trip
from routing.services.planner import Candidate, NoFeasibleRoute, plan_fuel_stops
from routing.services.route_client import RouteResult

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = FIXTURES / "golden_results.json"
ROUTES = ["chicago_la", "nyc_dallas"]


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def _check(actual: dict, key: str) -> None:
    if os.environ.get("UPDATE_GOLDEN"):
        golden = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
        golden[key] = actual
        GOLDEN.write_text(json.dumps(golden, indent=1, sort_keys=True) + "\n")
    assert actual == json.loads(GOLDEN.read_text())[key]


def _load_route(name: str) -> tuple[dict, RouteResult]:
    data = json.loads((FIXTURES / f"{name}_route.json").read_text())
    return data, RouteResult([(lat, lng) for lat, lng in data["coords"]], data["distance_miles"])


@pytest.fixture(scope="module")
def real_stations(django_db_setup, django_db_blocker):
    """The real cleaned + geocoded station table, loaded once per module (offline: the CSV and
    places file are committed) and removed afterwards."""
    sink = io.StringIO()
    with django_db_blocker.unblock():
        call_command("load_stations", stdout=sink)
        call_command("geocode_stations", stdout=sink)
        stations_service.invalidate_station_cache()
        yield
        FuelStation.objects.all().delete()
    cache.clear()
    stations_service.invalidate_station_cache()


@pytest.fixture(autouse=True)
def fresh_caches():
    cache.clear()
    stations_service.invalidate_station_cache()
    yield
    cache.clear()
    stations_service.invalidate_station_cache()


@pytest.mark.parametrize("name", ROUTES)
@pytest.mark.django_db
def test_real_route_results_are_unchanged(real_stations, name):
    data, route = _load_route(name)
    with patch.object(trip, "get_route", return_value=route):
        result = trip.plan_trip(data["start"], data["finish"])
    candidates = stations_service.candidates_along_route(route)
    coordinates = result["route"]["coordinates"]
    actual = {
        "distance_miles": result["distance_miles"],
        "candidates": {
            "count": len(candidates),
            "digest": _digest([[c.station_id, c.mile_marker, c.price, c.lat, c.lng] for c in candidates]),
        },
        "fuel_stops": result["fuel_stops"],
        "total_gallons_purchased": result["total_gallons_purchased"],
        "total_fuel_cost": result["total_fuel_cost"],
        "route": {"points": len(coordinates), "digest": _digest(coordinates)},
    }
    assert len(coordinates) <= trip.ROUTE_POINT_LIMIT
    _check(actual, name)


def _random_planner_results(seed: int = 4242, cases: int = 250) -> dict:
    """Plans for seeded random trips at three penalties, reduced to a digest."""
    rng = random.Random(seed)
    outcomes, feasible = [], 0
    for _ in range(cases):
        total = rng.uniform(1, 3000)
        cands = [
            Candidate(i, f"S{i}", "C", "ST", 0.0, 0.0, round(rng.uniform(2.5, 4.5), 3),
                      round(rng.uniform(-50, total + 100), 1))
            for i in range(rng.randint(0, 80))
        ]
        for penalty in (0, 2.0, 25.0):
            try:
                plan = plan_fuel_stops(total, cands, stop_penalty=penalty)
            except NoFeasibleRoute:
                outcomes.append("infeasible")
                continue
            feasible += 1
            outcomes.append([
                [(s.candidate.station_id, s.gallons_purchased, s.cost, s.fuel_on_arrival_gallons) for s in plan.stops],
                plan.total_gallons_purchased,
                plan.total_cost,
            ])
    return {"plans": len(outcomes), "feasible": feasible, "digest": _digest(outcomes)}


def test_random_planner_results_are_unchanged():
    actual = _random_planner_results()
    assert actual["feasible"] > 100
    _check(actual, "random_planner")
