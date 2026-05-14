"""
Route planning service.

Pipeline (3 external API calls max):
  1. GET Nominatim /search  (geocode start)      -> 1 call
  2. GET Nominatim /search  (geocode finish)     -> 1 call
  3. GET OSRM /route        (full polyline)      -> 1 call
  ─────────────────────────────────────────────────────────
  Total external calls: 3

All fuel selection is done locally from the bundled CSV.

Stop-selection algorithm:
  - Decode the OSRM polyline into a list of (lat, lng) points.
  - Walk the polyline accumulating miles.
  - Every time accumulated distance approaches MAX_RANGE (490 mi),
    determine the current US state and pick the cheapest station in that state.
  - Deduplicate consecutive stops in the same state.
  - Compute per-leg fuel cost using the price at the last fuelling point.
"""

import math
import time
import requests
from django.conf import settings

from .fuel_data import (
    FuelStation,
    haversine,
    get_stations,
    get_stations_by_state,
    cheapest_in_state,
    cheapest_near_point,
    point_to_state,
    STATE_CENTERS,
)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_BASE = getattr(settings, "OSRM_BASE_URL", "http://router.project-osrm.org")

MAX_RANGE = settings.VEHICLE_MAX_RANGE_MILES - 10   # 490 miles (10-mile buffer)
MPG = settings.VEHICLE_MPG                          # 10 mpg


# ──────────────────────────────────────────────────────────────────────────────
# Geocoding
# ──────────────────────────────────────────────────────────────────────────────

def _geocode(location: str) -> tuple[float, float]:
    """Geocode a US address/city to (lat, lng) using Nominatim."""
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": location, "format": "json", "limit": 1, "countrycodes": "us"},
        headers={"User-Agent": "FuelRoutePlanner/1.0 (assessment project)"},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise ValueError(f"Location not found: {location!r} — try adding city, state (e.g. 'Austin, TX')")
    r = results[0]
    return float(r["lat"]), float(r["lon"])


# ──────────────────────────────────────────────────────────────────────────────
# Polyline decoder (Google encoded polyline format, used by OSRM)
# ──────────────────────────────────────────────────────────────────────────────

def _decode_polyline(encoded: str) -> list[tuple[float, float]]:
    coords, index, lat, lng = [], 0, 0, 0
    while index < len(encoded):
        for is_lat in (True, False):
            b, shift, result = 0, 0, 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if is_lat:
                lat += delta
            else:
                lng += delta
        coords.append((lat / 1e5, lng / 1e5))
    return coords


# ──────────────────────────────────────────────────────────────────────────────
# OSRM routing
# ──────────────────────────────────────────────────────────────────────────────

def _osrm_route(
    start_lat: float, start_lng: float, end_lat: float, end_lng: float
) -> tuple[list[tuple[float, float]], float]:
    """
    Call OSRM once and return (decoded_polyline, distance_miles).
    """
    url = f"{OSRM_BASE}/route/v1/driving/{start_lng},{start_lat};{end_lng},{end_lat}"
    resp = requests.get(
        url,
        params={"overview": "full", "geometries": "polyline", "steps": "false"},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "Ok":
        raise ValueError(f"OSRM error: {data.get('message', data.get('code', 'unknown'))}")
    route = data["routes"][0]
    polyline = _decode_polyline(route["geometry"])
    distance_miles = route["distance"] / 1609.344
    return polyline, distance_miles


# ──────────────────────────────────────────────────────────────────────────────
# Fuel stop selection
# ──────────────────────────────────────────────────────────────────────────────

def _select_fuel_stops(
    polyline: list[tuple[float, float]],
    total_miles: float,
) -> list[FuelStation]:
    """
    Walk the decoded polyline and select cheapest fuel stops as needed.
    Uses state bounding-box lookup to identify the state at each sample point.
    """
    if total_miles <= MAX_RANGE:
        return []   # No stops needed — fits in one tank

    stops: list[FuelStation] = []
    accumulated = 0.0
    last_stop_state: str | None = None
    n = len(polyline)

    for i in range(1, n):
        seg = haversine(*polyline[i - 1], *polyline[i])
        accumulated += seg

        # Check if we need to stop (approaching range limit)
        # Look ahead: if the next sample would put us over range, stop now
        remaining_route = total_miles - (sum(
            haversine(*polyline[j - 1], *polyline[j])
            for j in range(1, i + 1)
        ))

        if accumulated + 10 >= MAX_RANGE and remaining_route > 50:
            # Determine current state
            lat, lng = polyline[i]
            state = point_to_state(lat, lng)

            if state and state != last_stop_state:
                station = cheapest_in_state(state)
                if station:
                    stops.append(station)
                    last_stop_state = state
                    accumulated = 0.0
            elif not state:
                # Point not in any state bbox — find closest station to this point
                station = cheapest_near_point(lat, lng, 150)
                if station and (not stops or stops[-1].id != station.id):
                    stops.append(station)
                    last_stop_state = None
                    accumulated = 0.0

    return stops


def _select_fuel_stops_v2(
    polyline: list[tuple[float, float]],
    total_miles: float,
) -> list[FuelStation]:
    """
    Improved stop selection: sample every N miles along the polyline,
    determine the state, find cheapest station there.
    Avoids recomputing cumulative sum from scratch on each iteration.
    """
    if total_miles <= MAX_RANGE:
        return []

    stops: list[FuelStation] = []
    miles_since_fill = 0.0
    last_state_used: str | None = None

    for i in range(1, len(polyline)):
        seg = haversine(*polyline[i - 1], *polyline[i])
        miles_since_fill += seg

        # Estimate miles remaining by linearly interpolating the fraction of
        # the polyline left (cheap and good enough).
        frac_done = i / len(polyline)
        miles_remaining = total_miles * (1 - frac_done)

        if miles_since_fill >= MAX_RANGE - 20 and miles_remaining > 30:
            lat, lng = polyline[i]
            state = point_to_state(lat, lng)

            if state and state != last_state_used:
                station = cheapest_in_state(state)
                if station and (not stops or stops[-1].state != state):
                    stops.append(station)
                    last_state_used = state
                    miles_since_fill = 0.0
            elif not state:
                station = cheapest_near_point(lat, lng, 200)
                if station and (not stops or stops[-1].id != station.id):
                    stops.append(station)
                    last_state_used = None
                    miles_since_fill = 0.0

    return stops


# ──────────────────────────────────────────────────────────────────────────────
# Cost calculation
# ──────────────────────────────────────────────────────────────────────────────

def _build_legs(
    start: tuple[float, float],
    start_label: str,
    finish: tuple[float, float],
    finish_label: str,
    stops: list[FuelStation],
    total_miles: float,
) -> tuple[list[dict], float]:
    """
    Build per-leg cost breakdown.
    We distribute total distance among legs proportionally using straight-line
    distances between consecutive stops (good approximation for summary).
    Fuel cost for each leg is calculated at the price paid at the start of
    that leg (i.e., the last stop's price, or an average if no prior stop).
    """
    all_st = get_stations()
    avg_price = sum(s.price for s in all_st) / len(all_st) if all_st else 3.50

    waypoints = [start] + [(s.lat, s.lng) for s in stops] + [finish]
    labels = [start_label] + [f"{s.name} – {s.city}, {s.state}" for s in stops] + [finish_label]
    prices = [avg_price] + [s.price for s in stops] + [0]  # price paid at each waypoint

    # Straight-line distances between waypoints
    raw_dists = [
        haversine(*waypoints[i], *waypoints[i + 1])
        for i in range(len(waypoints) - 1)
    ]
    raw_total = sum(raw_dists) or 1
    # Scale so legs sum to actual route distance
    scale = total_miles / raw_total

    legs = []
    total_cost = 0.0
    for i in range(len(waypoints) - 1):
        leg_miles = raw_dists[i] * scale
        leg_gallons = leg_miles / MPG
        fill_price = prices[i]  # price at departure point of this leg
        leg_cost = leg_gallons * fill_price
        total_cost += leg_cost
        legs.append({
            "from": labels[i],
            "to": labels[i + 1],
            "distance_miles": round(leg_miles, 1),
            "gallons_used": round(leg_gallons, 2),
            "price_per_gallon": round(fill_price, 4),
            "leg_fuel_cost_usd": round(leg_cost, 2),
        })

    return legs, round(total_cost, 2)


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def plan_route(start: str, finish: str) -> dict:
    """Plan a fuel-optimised route between two US locations."""

    # 1 & 2. Geocode (2 Nominatim calls)
    start_lat, start_lng = _geocode(start)
    end_lat, end_lng = _geocode(finish)

    # 3. Single OSRM call
    polyline, total_miles = _osrm_route(start_lat, start_lng, end_lat, end_lng)

    # Local computation — no more external calls
    stops = _select_fuel_stops_v2(polyline, total_miles)
    legs, total_cost = _build_legs(
        (start_lat, start_lng), f"Start: {start}",
        (end_lat, end_lng), f"End: {finish}",
        stops, total_miles,
    )

    total_gallons = total_miles / MPG

    # Thin the polyline for the response (keep every 5th point)
    thin = polyline[::5]
    if thin[-1] != polyline[-1]:
        thin.append(polyline[-1])

    return {
        "start": {"location": start, "lat": round(start_lat, 6), "lng": round(start_lng, 6)},
        "finish": {"location": finish, "lat": round(end_lat, 6), "lng": round(end_lng, 6)},
        "route_summary": {
            "total_distance_miles": round(total_miles, 1),
            "total_gallons_needed": round(total_gallons, 2),
            "total_fuel_cost_usd": total_cost,
            "number_of_fuel_stops": len(stops),
            "vehicle_max_range_miles": settings.VEHICLE_MAX_RANGE_MILES,
            "vehicle_mpg": MPG,
        },
        "fuel_stops": [s.to_dict() for s in stops],
        "legs": legs,
        "map": {
            "note": "Use polyline coordinates with any mapping library (e.g. Leaflet, Google Maps) to render the route.",
            "polyline_format": "[lat, lng] pairs — decode with Google Encoded Polyline format or use the pre-decoded array below.",
            "polyline_coords": [[round(lat, 5), round(lng, 5)] for lat, lng in thin],
            "markers": [
                {
                    "type": "start",
                    "label": f"START: {start}",
                    "lat": round(start_lat, 6),
                    "lng": round(start_lng, 6),
                },
                *[
                    {
                        "type": "fuel_stop",
                        "label": f"FUEL STOP #{i + 1}",
                        "lat": round(s.lat, 6),
                        "lng": round(s.lng, 6),
                        "station_name": s.name,
                        "price_per_gallon": round(s.price, 4),
                        "city": s.city,
                        "state": s.state,
                    }
                    for i, s in enumerate(stops)
                ],
                {
                    "type": "end",
                    "label": f"END: {finish}",
                    "lat": round(end_lat, 6),
                    "lng": round(end_lng, 6),
                },
            ],
        },
    }
