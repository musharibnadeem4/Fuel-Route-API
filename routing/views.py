import logging

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView, exception_handler

from .serializers import RouteRequestSerializer
from .services.geocode import PlaceNotFoundError
from .services.planner import NoFeasibleRoute
from .services.route_client import NoRouteFoundError, RouteServiceError
from .services.trip import SameLocationError, plan_trip

logger = logging.getLogger(__name__)

# (exception, HTTP status, error code); first match wins, so subclasses go first.
DOMAIN_ERRORS = [
    (PlaceNotFoundError, status.HTTP_400_BAD_REQUEST, "invalid_place"),
    (SameLocationError, status.HTTP_400_BAD_REQUEST, "same_location"),
    (NoFeasibleRoute, status.HTTP_422_UNPROCESSABLE_ENTITY, "no_feasible_route"),
    (NoRouteFoundError, status.HTTP_404_NOT_FOUND, "no_route_found"),
    (RouteServiceError, status.HTTP_502_BAD_GATEWAY, "routing_service_error"),
]


def error_response(code: str, message: str, http_status: int) -> Response:
    """Uniform error body: {"error": {"code", "message"}}."""
    return Response({"error": {"code": code, "message": message}}, status=http_status)


def _validation_message(detail: dict) -> str:
    parts = []
    for field, errors in detail.items():
        errors = errors if isinstance(errors, list) else [errors]
        parts.extend(f"{field}: {e}" for e in errors)
    return "; ".join(parts)


def api_exception_handler(exc: Exception, context: dict) -> Response:
    """DRF exception handler producing the uniform error shape; never leaks a traceback."""
    response = exception_handler(exc, context)
    if response is None:
        logger.error("Unhandled error in API view", exc_info=exc)
        return error_response("internal_error", "Unexpected server error.", 500)
    if isinstance(exc, ValidationError) and isinstance(response.data, dict):
        return error_response("invalid_input", _validation_message(response.data), response.status_code)
    message = response.data.get("detail", str(exc)) if isinstance(response.data, dict) else str(exc)
    code = "invalid_input" if response.status_code == 400 else getattr(exc, "default_code", "error")
    return error_response(code, str(message), response.status_code)


@api_view(["GET"])
def health(request: Request) -> Response:
    """Liveness probe."""
    return Response({"status": "ok"})


class RouteView(APIView):
    """POST {"start", "finish"} -> route, cheapest fuel stops and total fuel cost."""

    authentication_classes: list = []
    permission_classes: list = []
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]

    def post(self, request: Request) -> Response:
        serializer = RouteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = plan_trip(serializer.validated_data["start"], serializer.validated_data["finish"])
        except tuple(exc for exc, _, _ in DOMAIN_ERRORS) as exc:
            http_status, code = next((s, c) for e, s, c in DOMAIN_ERRORS if isinstance(exc, e))
            return error_response(code, str(exc), http_status)
        return Response(payload)
