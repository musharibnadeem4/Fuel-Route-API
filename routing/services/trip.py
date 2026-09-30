"""Trip orchestration: places -> cache -> ONE route call -> stations -> plan -> payload."""
import hashlib
import logging
import time

from django.conf import settings
from django.core.cache import cache
from shapely.geometry import LineString

from .geocode import Place, lookup_place
from .planner import Plan, plan_fuel_stops
from .route_client import RouteResult, get_route
from .stations import candidates_along_route

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 24 * 60 * 60
RANGE_MILES = 500.0
MPG = 10.0
ROUTE_POINT_LIMIT = 1500


class SameLocationError(ValueError):
    """Start and finish resolve to the same coordinates."""


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def _cache_key(start: Place, finish: Place, corridor_miles: float, stop_penalty: float) -> str:
    """Key on resolved coordinates (what the route depends on), not on the raw text.

    "chicago, il", "Chicago, IL " and any alias that resolves to the same point share
    one entry. The corridor and stop penalty are part of the key so changing a setting
    can't serve stale plans.
    """
    raw = f"v2|{corridor_miles:g}|{stop_penalty:g}|{start.lat:.5f},{start.lng:.5f}|{finish.lat:.5f},{finish.lng:.5f}"
    return "trip:" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def simplify_route(coords: list[tuple[float, float]], max_points: int = ROUTE_POINT_LIMIT) -> list[list[float]]:
    """Return GeoJSON-order [[lng, lat], ...] with at most max_points, first/last kept.

    Uses Douglas-Peucker with a tolerance that doubles until the point limit is met,
    so the shape is preserved better than with a fixed stride.
    """
    points = [(lng, lat) for lat, lng in coords]
    if len(points) > max_points:
        line = LineString(points)
        tolerance = 0.0005  # degrees, roughly 50 m
        while True:
            points = list(line.simplify(tolerance, preserve_topology=False).coords)
            if len(points) <= max_points:
                break
            tolerance *= 2
    return [[round(lng, 5), round(lat, 5)] for lng, lat in points]


def _place_dict(place: Place) -> dict:
    return {"name": place.name, "lat": round(place.lat, 5), "lng": round(place.lng, 5)}


def build_payload(
    start: Place, finish: Place, route: RouteResult, plan: Plan, corridor_miles: float, stop_penalty: float
) -> dict:
    """The cacheable response body (everything except per-request meta)."""
    return {
        "start": _place_dict(start),
        "finish": _place_dict(finish),
        "distance_miles": round(route.distance_miles, 2),
        "route": {"type": "LineString", "coordinates": simplify_route(route.coords)},
        "fuel_stops": [
            {
                "order": i,
                "name": s.candidate.name,
                "city": s.candidate.city,
                "state": s.candidate.state,
                "lat": s.candidate.lat,
                "lng": s.candidate.lng,
                "price_per_gallon": round(s.candidate.price, 4),
                "mile_marker": round(s.candidate.mile_marker, 1),
                "gallons_purchased": s.gallons_purchased,
                "cost": s.cost,
            }
            for i, s in enumerate(plan.stops, start=1)
        ],
        "total_gallons_purchased": plan.total_gallons_purchased,
        "total_fuel_cost": plan.total_cost,
        "assumptions": {
            "range_miles": int(RANGE_MILES),
            "mpg": int(MPG),
            "start_tank": "full, not charged",
            "corridor_miles": corridor_miles,
            "stop_penalty_usd": stop_penalty,
            "price_rule": "lowest price per station",
        },
    }


def plan_trip(start: str, finish: str) -> dict:
    """Plan fuel stops for a trip given "City, ST" or "lat,lng" strings.

    Makes at most one external call (the route), and none on a cache hit.
    Raises PlaceNotFoundError, SameLocationError, NoRouteFoundError,
    RouteServiceError or NoFeasibleRoute; callers map these to HTTP statuses.
    """
    began = time.perf_counter()
    origin, destination = lookup_place(start), lookup_place(finish)
    if (origin.lat, origin.lng) == (destination.lat, destination.lng):
        raise SameLocationError("Start and finish are the same place.")
    corridor = settings.STATION_CORRIDOR_MILES
    penalty = float(settings.FUEL_STOP_PENALTY)
    key = _cache_key(origin, destination, corridor, penalty)

    cached = cache.get(key)
    if cached is not None:
        total = _ms(began)
        logger.info("trip served from cache: %s -> %s total_ms=%s", origin.name, destination.name, total)
        meta = {"external_api_calls": 0, "cached": True, "compute_ms": total, "route_call_ms": 0.0}
        return {**cached, "start": _place_dict(origin), "finish": _place_dict(destination), "meta": meta}

    step = time.perf_counter()
    route = get_route(origin.lat, origin.lng, destination.lat, destination.lng)
    route_ms = _ms(step)

    step = time.perf_counter()
    candidates = candidates_along_route(route, corridor)
    matching_ms = _ms(step)

    step = time.perf_counter()
    plan = plan_fuel_stops(
        route.distance_miles, candidates, range_miles=RANGE_MILES, mpg=MPG, stop_penalty=penalty
    )
    planning_ms = _ms(step)

    payload = build_payload(origin, destination, route, plan, corridor, penalty)
    cache.set(key, payload, CACHE_TTL_SECONDS)
    total = _ms(began)
    logger.info(
        "trip planned: %s -> %s route_call_ms=%s matching_ms=%s planning_ms=%s total_ms=%s "
        "candidates=%d stops=%d",
        origin.name, destination.name, route_ms, matching_ms, planning_ms, total,
        len(candidates), len(plan.stops),
    )
    meta = {
        "external_api_calls": 1,
        "cached": False,
        "compute_ms": round(total - route_ms, 1),  # our own work; the external call is separate
        "route_call_ms": route_ms,
    }
    return {**payload, "meta": meta}
