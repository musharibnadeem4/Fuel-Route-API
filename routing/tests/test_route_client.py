import pytest
import requests

from routing.services import route_client
from routing.services.route_client import NoRouteFoundError, RouteServiceError, get_route

KEY = "SECRET-KEY-123"
OK_BODY = {
    "features": [
        {
            "geometry": {"coordinates": [[-95.0, 40.0], [-94.5, 40.5, 300.0], [-94.0, 41.0]]},
            "properties": {"summary": {"distance": 160934.4, "duration": 5000}},
        }
    ]
}
ONE_POINT_BODY = {
    "features": [
        {"geometry": {"coordinates": [[-95, 40]]}, "properties": {"summary": {"distance": 5}}}
    ]
}


class FakeResponse:
    def __init__(self, status_code=200, body=None, bad_json=False):
        self.status_code, self._body, self._bad_json = status_code, body, bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._body


@pytest.fixture(autouse=True)
def api_key(settings):
    settings.ORS_API_KEY = KEY


@pytest.fixture
def post(monkeypatch):
    """Replace the shared session's POST; set post.outcome["value"] to a response or exception."""
    calls = []
    outcome = {"value": FakeResponse(200, OK_BODY)}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if isinstance(outcome["value"], Exception):
            raise outcome["value"]
        return outcome["value"]

    monkeypatch.setattr(route_client._session, "post", fake_post)
    fake_post.calls, fake_post.outcome = calls, outcome
    return fake_post


def test_success_converts_coords_and_distance(post):
    result = get_route(40.0, -95.0, 41.0, -94.0)
    assert result.coords == [(40.0, -95.0), (40.5, -94.5), (41.0, -94.0)]  # (lat, lng), no elevation
    assert result.distance_miles == pytest.approx(100.0)  # 160,934.4 m


def test_makes_exactly_one_correctly_formed_request(post):
    get_route(40.0, -95.0, 41.0, -94.0)
    assert len(post.calls) == 1
    url, kwargs = post.calls[0]
    assert url == "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    assert kwargs["headers"] == {"Authorization": KEY}
    assert kwargs["json"] == {
        "coordinates": [[-95.0, 40.0], [-94.0, 41.0]],  # [lng, lat]
        "radiuses": [-1, -1],
        "instructions": False,
    }
    assert kwargs["timeout"] == (3, 15)


ROUTABLE_2010 = {"error": {"code": 2010, "message": "Could not find routable point within a radius of 350.0 meters"}}

FAILURES = [
    (FakeResponse(401, {"error": "Access to this API has been disallowed"}), RouteServiceError, "API key"),
    (FakeResponse(403, {}), RouteServiceError, "API key"),
    (FakeResponse(404, {"error": {"code": 2009, "message": "Route could not be found"}}), NoRouteFoundError, "No drivable route"),
    (FakeResponse(404, ROUTABLE_2010), NoRouteFoundError, "No drivable route"),
    (FakeResponse(400, ROUTABLE_2010), NoRouteFoundError, "No drivable route"),
    (FakeResponse(400, {"error": "Could not find routable point near coordinate 0"}), NoRouteFoundError, "No drivable route"),
    (FakeResponse(429, {}), RouteServiceError, "rate limit"),
    (FakeResponse(503, None, bad_json=True), RouteServiceError, "unavailable"),
    (FakeResponse(400, {"error": {"code": 2004, "message": "Distance exceeds limit"}}), RouteServiceError, "Distance exceeds limit"),
    (FakeResponse(200, None, bad_json=True), RouteServiceError, "unexpected response"),
    (FakeResponse(200, {"features": []}), RouteServiceError, "unexpected response"),
    (FakeResponse(200, {"features": [{"geometry": {}}]}), RouteServiceError, "unexpected response"),
    (FakeResponse(200, {"error": "weird"}), RouteServiceError, "unexpected response"),
    (FakeResponse(200, ONE_POINT_BODY), RouteServiceError, "empty route"),
    (requests.Timeout("read timed out"), RouteServiceError, "timed out"),
    (requests.ConnectionError(f"boom {KEY}"), RouteServiceError, "Could not reach"),
]


@pytest.mark.parametrize("response, exc_type, fragment", FAILURES)
def test_failures_raise_typed_errors_without_leaking_the_key(post, response, exc_type, fragment):
    post.outcome["value"] = response
    with pytest.raises(exc_type) as exc:
        get_route(40.0, -95.0, 41.0, -94.0)
    assert fragment in str(exc.value)
    assert KEY not in str(exc.value)
    assert len(post.calls) == 1  # never retried


def test_unroutable_is_a_route_service_error_subclass():
    assert issubclass(NoRouteFoundError, RouteServiceError)


def test_error_text_echoing_the_key_is_scrubbed(post):
    post.outcome["value"] = FakeResponse(400, {"error": f"bad token {KEY} supplied"})
    with pytest.raises(RouteServiceError) as exc:
        get_route(40.0, -95.0, 41.0, -94.0)
    assert KEY not in str(exc.value)
    assert "***" in str(exc.value)


def test_status_code_is_exposed(post):
    post.outcome["value"] = FakeResponse(401, {})
    with pytest.raises(RouteServiceError) as exc:
        get_route(40.0, -95.0, 41.0, -94.0)
    assert exc.value.status_code == 401


def test_missing_api_key_fails_before_any_request(post, settings):
    settings.ORS_API_KEY = ""
    with pytest.raises(RouteServiceError, match="not configured"):
        get_route(40.0, -95.0, 41.0, -94.0)
    assert post.calls == []


def test_key_not_logged(post, caplog):
    post.outcome["value"] = FakeResponse(401, {"error": f"token {KEY}"})
    with pytest.raises(RouteServiceError):
        get_route(40.0, -95.0, 41.0, -94.0)
    assert KEY not in caplog.text
