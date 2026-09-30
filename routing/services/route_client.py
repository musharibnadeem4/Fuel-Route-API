"""Client for the OpenRouteService directions API (one POST per uncached route)."""
import logging
from dataclasses import dataclass
from functools import cached_property

import numpy as np
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

ORS_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
METERS_PER_MILE = 1609.344
TIMEOUT = (3, 15)  # connect, read (seconds)
_ROUTABLE_CODES = {2009, 2010}  # ORS: route not found / no routable point near coordinate

_session = requests.Session()


class RouteServiceError(Exception):
    """The routing service failed or returned something unusable."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class NoRouteFoundError(RouteServiceError):
    """No drivable route exists between the given points."""


@dataclass(frozen=True)
class RouteResult:
    """A driving route: (lat, lng) vertices and total length in miles."""

    coords: list[tuple[float, float]]
    distance_miles: float

    @cached_property
    def array(self) -> np.ndarray:
        """(n, 2) float array of (lat, lng), built once and shared by matching and simplifying."""
        return np.asarray(self.coords, dtype=np.float64)


def _scrub(text: str, api_key: str) -> str:
    """Remove the API key from text that may be shown to a caller."""
    return text.replace(api_key, "***") if api_key else text


def _error_details(response: requests.Response) -> tuple[int | None, str]:
    """Extract (ORS error code, message) from an error body; tolerant of any shape."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return None, ""
    if isinstance(error, dict):
        code = error.get("code")
        return (code if isinstance(code, int) else None), str(error.get("message", ""))
    return None, str(error or "")


def _raise_for_status(response: requests.Response, api_key: str) -> None:
    status = response.status_code
    if status == 200:
        return
    code, message = _error_details(response)
    logger.warning("ORS request failed: HTTP %s, code %s", status, code)
    if status == 404 or code in _ROUTABLE_CODES or "routable point" in message.lower():
        raise NoRouteFoundError("No drivable route found between those locations.", status)
    if status in (401, 403):
        raise RouteServiceError(f"Routing service rejected the API key (HTTP {status}).", status)
    if status == 429:
        raise RouteServiceError("Routing service rate limit reached; try again later.", status)
    if status >= 500:
        raise RouteServiceError(f"Routing service unavailable (HTTP {status}).", status)
    detail = f": {_scrub(message, api_key)[:200]}" if message else "."
    raise RouteServiceError(f"Routing service rejected the request (HTTP {status}){detail}", status)


def _parse_route(payload: object) -> RouteResult:
    """Pull geometry and distance out of a GeoJSON directions response."""
    try:
        feature = payload["features"][0]  # type: ignore[index]
        raw = feature["geometry"]["coordinates"]
        meters = float(feature["properties"]["summary"]["distance"])
        coords = [(float(p[1]), float(p[0])) for p in raw]  # ORS is [lng, lat(, elevation)]
    except (KeyError, IndexError, TypeError, ValueError):
        raise RouteServiceError("Routing service returned an unexpected response.") from None
    if len(coords) < 2 or meters <= 0:
        raise RouteServiceError("Routing service returned an empty route.")
    return RouteResult(coords=coords, distance_miles=meters / METERS_PER_MILE)


def get_route(start_lat: float, start_lng: float, end_lat: float, end_lng: float) -> RouteResult:
    """Fetch the driving route between two points with a single ORS call.

    radiuses = -1 lets endpoints snap to the nearest road from a city centroid.
    Raises NoRouteFoundError if no route exists, RouteServiceError for any other failure.
    """
    api_key = settings.ORS_API_KEY
    if not api_key:
        raise RouteServiceError("ORS_API_KEY is not configured.")
    body = {
        "coordinates": [[start_lng, start_lat], [end_lng, end_lat]],
        "radiuses": [-1, -1],
        "instructions": False,
    }
    try:
        response = _session.post(
            ORS_URL, json=body, headers={"Authorization": api_key}, timeout=TIMEOUT
        )
    except requests.Timeout:
        raise RouteServiceError("Routing service timed out.") from None
    except requests.RequestException:
        raise RouteServiceError("Could not reach the routing service.") from None
    _raise_for_status(response, api_key)
    try:
        payload = response.json()
    except ValueError:
        raise RouteServiceError("Routing service returned an unexpected response.") from None
    return _parse_route(payload)
