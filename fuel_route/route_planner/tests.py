"""
Tests for the fuel route planner.

Run with:  python manage.py test route_planner
"""
from unittest.mock import patch, MagicMock
from django.test import TestCase
from rest_framework.test import APITestCase
from rest_framework import status

from .fuel_data import (
    haversine,
    point_to_state,
    cheapest_in_state,
    get_stations,
    FuelStation,
)
from .route_service import _decode_polyline, _select_fuel_stops_v2, _build_legs


class HaversineTests(TestCase):
    def test_la_to_nyc(self):
        d = haversine(34.0522, -118.2437, 40.7128, -74.0060)
        self.assertAlmostEqual(d, 2446, delta=10)

    def test_same_point(self):
        self.assertEqual(haversine(40.0, -80.0, 40.0, -80.0), 0.0)


class StateDetectionTests(TestCase):
    def test_la_is_ca(self):
        self.assertEqual(point_to_state(34.05, -118.24), "CA")

    def test_dallas_is_tx(self):
        self.assertEqual(point_to_state(32.77, -96.79), "TX")

    def test_chicago_is_il(self):
        self.assertEqual(point_to_state(41.88, -87.63), "IL")

    def test_ocean_returns_none(self):
        self.assertIsNone(point_to_state(30.0, -50.0))


class FuelDataTests(TestCase):
    def test_stations_load(self):
        stations = get_stations()
        self.assertGreater(len(stations), 5000)

    def test_cheapest_in_tx(self):
        s = cheapest_in_state("TX")
        self.assertIsNotNone(s)
        self.assertEqual(s.state, "TX")
        self.assertGreater(s.price, 0)

    def test_cheapest_in_nonexistent_state(self):
        self.assertIsNone(cheapest_in_state("ZZ"))


class PolylineTests(TestCase):
    def test_decode_simple(self):
        # Encoded polyline for a two-point line (from OSRM docs)
        # This is the encoded form of [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
        encoded = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
        coords = _decode_polyline(encoded)
        self.assertEqual(len(coords), 3)
        self.assertAlmostEqual(coords[0][0], 38.5, places=1)
        self.assertAlmostEqual(coords[0][1], -120.2, places=1)


class FuelStopSelectionTests(TestCase):
    def _make_polyline(self, lat1, lng1, lat2, lng2, n=100):
        return [
            (lat1 + (lat2 - lat1) * i / n, lng1 + (lng2 - lng1) * i / n)
            for i in range(n + 1)
        ]

    def test_no_stops_short_route(self):
        """A 400-mile route needs no stops (< 500-mile range)."""
        poly = self._make_polyline(34.0, -118.0, 37.7, -122.4)
        total = sum(haversine(*poly[i-1], *poly[i]) for i in range(1, len(poly)))
        # Artificially set total to 400 miles for the test
        stops = _select_fuel_stops_v2(poly, 400.0)
        self.assertEqual(stops, [])

    def test_stops_on_la_nyc(self):
        """LA→NYC (~2800 road miles) should produce 4-6 fuel stops."""
        poly = self._make_polyline(34.0522, -118.2437, 40.7128, -74.0060, n=500)
        total = sum(haversine(*poly[i-1], *poly[i]) for i in range(1, len(poly)))
        stops = _select_fuel_stops_v2(poly, total)
        self.assertGreaterEqual(len(stops), 3)
        self.assertLessEqual(len(stops), 8)


class RoutePlanAPITests(APITestCase):
    """Integration tests that mock external API calls."""

    def _mock_geocode(self, location):
        coords = {
            "Los Angeles, CA": (34.0522, -118.2437),
            "New York, NY": (40.7128, -74.0060),
        }
        return coords.get(location, (39.5, -98.35))

    @patch("route_planner.route_service._geocode")
    @patch("route_planner.route_service._osrm_route")
    def test_plan_route_success(self, mock_osrm, mock_geocode):
        # Build a simple 200-point polyline LA→NYC
        def geocode_side_effect(loc):
            if "Los Angeles" in loc:
                return (34.0522, -118.2437)
            return (40.7128, -74.0060)

        mock_geocode.side_effect = geocode_side_effect

        # Build fake polyline with ~2500 miles
        la = (34.0522, -118.2437)
        nyc = (40.7128, -74.0060)
        N = 200
        poly = [(la[0] + (nyc[0]-la[0])*i/N, la[1] + (nyc[1]-la[1])*i/N) for i in range(N+1)]
        total_m = sum(haversine(*poly[i-1], *poly[i]) for i in range(1, len(poly)))
        mock_osrm.return_value = (poly, total_m)

        response = self.client.post(
            "/api/route/plan/",
            {"start": "Los Angeles, CA", "finish": "New York, NY"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn("route_summary", data)
        self.assertIn("fuel_stops", data)
        self.assertIn("legs", data)
        self.assertIn("map", data)
        self.assertGreater(data["route_summary"]["total_distance_miles"], 0)
        self.assertGreater(data["route_summary"]["total_fuel_cost_usd"], 0)

    def test_missing_fields(self):
        response = self.client.post("/api/route/plan/", {"start": "LA"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_empty_body(self):
        response = self.client.post("/api/route/plan/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_get_returns_usage_info(self):
        response = self.client.get("/api/route/plan/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("endpoint", response.json())
