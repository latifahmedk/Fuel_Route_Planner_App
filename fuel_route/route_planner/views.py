import time
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RoutePlanRequestSerializer
from .route_service import plan_route


class RoutePlanView(APIView):
    """
    POST /api/route/plan/

    Body:
        {
            "start": "Los Angeles, CA",
            "finish": "New York, NY"
        }

    Returns a route with optimal fuel stops (cheapest price per gallon
    within range) and total fuel cost estimate.
    """

    def post(self, request, *args, **kwargs):
        serializer = RoutePlanRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": "Invalid input", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        start = serializer.validated_data["start"]
        finish = serializer.validated_data["finish"]

        t0 = time.perf_counter()
        try:
            result = plan_route(start, finish)
        except ValueError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except Exception as exc:
            return Response(
                {"error": "Routing service error", "details": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        elapsed_ms = round((time.perf_counter() - t0) * 1000)

        result["_meta"] = {
            "computation_time_ms": elapsed_ms,
            "external_api_calls": "2 geocode (Nominatim) + 1 route (OSRM) = 3 total",
            "data_source": "OPIS Fuel Prices CSV + OSRM Routing + Nominatim Geocoding",
        }

        return Response(result, status=status.HTTP_200_OK)

    def get(self, request, *args, **kwargs):
        """Health-check / usage info."""
        return Response(
            {
                "endpoint": "POST /api/route/plan/",
                "description": "Plan a fuel-optimised route between two US locations",
                "body_params": {
                    "start": "Starting location (string, required)",
                    "finish": "Destination location (string, required)",
                },
                "example": {
                    "start": "Los Angeles, CA",
                    "finish": "New York, NY",
                },
            }
        )
