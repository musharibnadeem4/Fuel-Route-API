"""Match stations to a route: which stations lie near it, and at which mile marker.

Station arrays are loaded from the DB once and cached in module state, so a request
performs no DB scan. Per request we build a KD-tree over the (downsampled) route
vertices and query every station once, so there is no stations x vertices loop.

Coordinates are converted to 3-D chord coordinates in miles (points on a sphere of
Earth radius). Unlike an equirectangular x/y plane this has no longitude scale
distortion on routes that span many latitudes, and for distances of a few miles the
chord is indistinguishable from the great-circle arc.
"""
import threading
from dataclasses import dataclass

import numpy as np
from django.conf import settings
from django.db.models import FloatField
from django.db.models.functions import Cast
from scipy.spatial import cKDTree

from routing.models import FuelStation

from .planner import Candidate
from .route_client import RouteResult

EARTH_RADIUS_MILES = 3958.7613
MAX_ROUTE_VERTICES = 4000


@dataclass(frozen=True)
class StationArrays:
    """Columnar station data; xyz is precomputed so requests only do the tree query."""

    ids: np.ndarray
    lat: np.ndarray
    lng: np.ndarray
    price: np.ndarray
    xyz: np.ndarray
    names: list[str]
    cities: list[str]
    states: list[str]

    def __len__(self) -> int:
        return len(self.ids)


_cache: StationArrays | None = None
_cache_lock = threading.Lock()


def to_xyz(lat: np.ndarray, lng: np.ndarray) -> np.ndarray:
    """Convert degrees to (n, 3) chord coordinates in miles."""
    lat_r, lng_r = np.radians(lat), np.radians(lng)
    cos_lat = np.cos(lat_r)
    return EARTH_RADIUS_MILES * np.column_stack(
        (cos_lat * np.cos(lng_r), cos_lat * np.sin(lng_r), np.sin(lat_r))
    )


def build_station_arrays(rows: list[tuple]) -> StationArrays:
    """Build arrays from (opis_id, name, city, state, price, lat, lng) rows."""
    ids = np.array([r[0] for r in rows], dtype=np.int64)
    price = np.array([float(r[4]) for r in rows], dtype=np.float64)
    lat = np.array([r[5] for r in rows], dtype=np.float64)
    lng = np.array([r[6] for r in rows], dtype=np.float64)
    return StationArrays(
        ids, lat, lng, price, to_xyz(lat, lng),
        [r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows],
    )


def get_station_arrays() -> StationArrays:
    """Return the cached station arrays, loading them from the DB on first use.

    Stations without exact coordinates (missing, or state-centroid fallback) are
    excluded: their position says nothing about the route.
    """
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                # CAST in SQL: avoids building 6.6k Decimal objects just to turn them into floats
                rows = (
                    FuelStation.objects.filter(lat__isnull=False, lng__isnull=False, geo_approx=False)
                    .annotate(price_f=Cast("price", FloatField()))
                    .values_list("opis_id", "name", "city", "state", "price_f", "lat", "lng")
                )
                _cache = build_station_arrays(list(rows))
    return _cache


def invalidate_station_cache() -> None:
    """Drop the cached arrays; the next request reloads them from the DB."""
    global _cache
    with _cache_lock:
        _cache = None


def _haversine_miles(lat1, lng1, lat2, lng2) -> np.ndarray:
    lat1, lng1, lat2, lng2 = map(np.radians, (lat1, lng1, lat2, lng2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a))


def route_mile_markers(lat: np.ndarray, lng: np.ndarray, distance_miles: float) -> np.ndarray:
    """Cumulative haversine miles per vertex, scaled so the last equals distance_miles."""
    segments = _haversine_miles(lat[:-1], lng[:-1], lat[1:], lng[1:])
    markers = np.concatenate(([0.0], np.cumsum(segments)))
    if markers[-1] > 0:
        markers *= distance_miles / markers[-1]
    return markers


def route_points(route: RouteResult) -> tuple[np.ndarray, np.ndarray]:
    """Return (xyz, mile_markers) for the route, downsampled if it is very dense.

    Markers are computed on the full geometry first, so downsampling (which keeps
    the first and last vertex) does not change the distances.
    """
    coords = route.array
    lat, lng = coords[:, 0], coords[:, 1]
    markers = route_mile_markers(lat, lng, route.distance_miles)
    if len(coords) > MAX_ROUTE_VERTICES:
        keep = np.linspace(0, len(coords) - 1, MAX_ROUTE_VERTICES).round().astype(np.intp)
        lat, lng, markers = lat[keep], lng[keep], markers[keep]
    return to_xyz(lat, lng), markers


def find_candidates(route: RouteResult, stations: StationArrays, corridor_miles: float) -> list[Candidate]:
    """Stations within corridor_miles of a route vertex, sorted by mile marker."""
    if len(stations) == 0:
        return []
    route_xyz, markers = route_points(route)
    distance, vertex = cKDTree(route_xyz).query(
        stations.xyz, k=1, distance_upper_bound=corridor_miles
    )
    hits = np.flatnonzero(np.isfinite(distance))  # misses come back as inf
    hits = hits[np.argsort(markers[vertex[hits]], kind="stable")]
    return [
        Candidate(
            station_id=int(stations.ids[i]),
            name=stations.names[i],
            city=stations.cities[i],
            state=stations.states[i],
            lat=float(stations.lat[i]),
            lng=float(stations.lng[i]),
            price=float(stations.price[i]),
            mile_marker=float(markers[vertex[i]]),
        )
        for i in hits
    ]


def candidates_along_route(route: RouteResult, corridor_miles: float | None = None) -> list[Candidate]:
    """Candidates for the planner: cached stations within the corridor, by mile marker."""
    if corridor_miles is None:
        corridor_miles = settings.STATION_CORRIDOR_MILES
    return find_candidates(route, get_station_arrays(), corridor_miles)
