"""
Fuel data loader with state-indexed lookup.

Stations are grouped by US state. Route selection is done by matching
route waypoints to states (via reverse geocoding the waypoint OR via
a state-boundary bounding box lookup).
"""

import csv
import math
import threading
from django.conf import settings

# -----------------------------------------------------------------------
# US state bounding boxes  {state: (min_lat, max_lat, min_lng, max_lng)}
# -----------------------------------------------------------------------
STATE_BBOX = {
    "AL": (30.19, 35.01, -88.47, -84.88),
    "AK": (54.54, 71.38, -179.15, -129.99),
    "AZ": (31.33, 37.00, -114.82, -109.04),
    "AR": (33.00, 36.50, -94.62, -89.64),
    "CA": (32.53, 42.01, -124.41, -114.13),
    "CO": (36.99, 41.00, -109.06, -102.04),
    "CT": (40.98, 42.05, -73.73, -71.79),
    "DE": (38.45, 39.84, -75.79, -75.05),
    "FL": (24.52, 31.00, -87.63, -80.03),
    "GA": (30.36, 35.00, -85.61, -80.84),
    "HI": (18.91, 28.40, -178.33, -154.81),
    "ID": (41.99, 49.00, -117.24, -111.04),
    "IL": (36.97, 42.51, -91.51, -87.02),
    "IN": (37.77, 41.76, -88.10, -84.78),
    "IA": (40.38, 43.50, -96.64, -90.14),
    "KS": (36.99, 40.00, -102.05, -94.59),
    "KY": (36.50, 39.15, -89.57, -81.96),
    "LA": (28.93, 33.02, -94.04, -88.82),
    "ME": (42.98, 47.46, -71.08, -66.95),
    "MD": (37.91, 39.72, -79.49, -75.05),
    "MA": (41.24, 42.89, -73.50, -69.93),
    "MI": (41.70, 48.19, -90.42, -82.42),
    "MN": (43.50, 49.38, -97.24, -89.49),
    "MS": (30.17, 35.00, -91.65, -88.10),
    "MO": (35.99, 40.61, -95.77, -89.10),
    "MT": (44.36, 49.00, -116.05, -104.04),
    "NE": (40.00, 43.00, -104.05, -95.31),
    "NV": (35.00, 42.00, -120.00, -114.04),
    "NH": (42.70, 45.31, -72.56, -70.70),
    "NJ": (38.93, 41.36, -75.56, -73.89),
    "NM": (31.33, 37.00, -109.05, -103.00),
    "NY": (40.50, 45.02, -79.76, -71.86),
    "NC": (33.84, 36.59, -84.32, -75.46),
    "ND": (45.93, 49.00, -104.05, -96.55),
    "OH": (38.40, 41.98, -84.82, -80.52),
    "OK": (33.62, 37.00, -103.00, -94.43),
    "OR": (41.99, 46.24, -124.57, -116.46),
    "PA": (39.72, 42.27, -80.52, -74.69),
    "RI": (41.15, 42.02, -71.91, -71.12),
    "SC": (32.05, 35.22, -83.35, -78.54),
    "SD": (42.48, 45.95, -104.06, -96.44),
    "TN": (34.99, 36.68, -90.31, -81.65),
    "TX": (25.84, 36.50, -106.65, -93.51),
    "UT": (36.99, 42.00, -114.05, -109.04),
    "VT": (42.73, 45.02, -73.44, -71.46),
    "VA": (36.54, 39.47, -83.68, -75.24),
    "WA": (45.55, 49.00, -124.74, -116.92),
    "WV": (37.20, 40.64, -82.64, -77.72),
    "WI": (42.49, 47.08, -92.89, -86.25),
    "WY": (40.99, 45.00, -111.06, -104.05),
    "DC": (38.79, 38.99, -77.12, -76.91),
}

# State geographic centers for polyline state detection
STATE_CENTERS = {
    "AL": (32.806671, -86.791130), "AK": (61.370716, -152.404419),
    "AZ": (33.729759, -111.431221), "AR": (34.969704, -92.373123),
    "CA": (36.116203, -119.681564), "CO": (39.059811, -105.311104),
    "CT": (41.597782, -72.755371), "DE": (39.318523, -75.507141),
    "FL": (27.766279, -81.686783), "GA": (33.040619, -83.643074),
    "HI": (21.094318, -157.498337), "ID": (44.240459, -114.478828),
    "IL": (40.349457, -88.986137), "IN": (39.849426, -86.258278),
    "IA": (42.011539, -93.210526), "KS": (38.526600, -96.726486),
    "KY": (37.668140, -84.670067), "LA": (31.169960, -91.867805),
    "ME": (44.693947, -69.381927), "MD": (39.063946, -76.802101),
    "MA": (42.230171, -71.530106), "MI": (43.326618, -84.536095),
    "MN": (45.694454, -93.900192), "MS": (32.741646, -89.678696),
    "MO": (38.456085, -92.288368), "MT": (46.921925, -110.454353),
    "NE": (41.125370, -98.268082), "NV": (38.313515, -117.055374),
    "NH": (43.452492, -71.563896), "NJ": (40.298904, -74.521011),
    "NM": (34.840515, -106.248482), "NY": (42.165726, -74.948051),
    "NC": (35.630066, -79.806419), "ND": (47.528912, -99.784012),
    "OH": (40.388783, -82.764915), "OK": (35.565342, -96.928917),
    "OR": (44.572021, -122.070938), "PA": (40.590752, -77.209755),
    "RI": (41.680893, -71.511780), "SC": (33.856892, -80.945007),
    "SD": (44.299782, -99.438828), "TN": (35.747845, -86.692345),
    "TX": (31.054487, -97.563461), "UT": (40.150032, -111.862434),
    "VT": (44.045876, -72.710686), "VA": (37.769337, -78.169968),
    "WA": (47.400902, -121.490494), "WV": (38.491226, -80.954453),
    "WI": (44.268543, -89.616508), "WY": (42.755966, -107.302490),
    "DC": (38.897438, -77.026817),
}

US_STATES = set(STATE_BBOX.keys())


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in miles between two lat/lng points."""
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def point_to_state(lat: float, lng: float) -> str | None:
    """Return the US state abbreviation for a lat/lng point, or None."""
    for state, (min_lat, max_lat, min_lng, max_lng) in STATE_BBOX.items():
        if min_lat <= lat <= max_lat and min_lng <= lng <= max_lng:
            return state
    return None


class FuelStation:
    __slots__ = ('id', 'name', 'address', 'city', 'state', 'price', 'lat', 'lng')

    def __init__(self, row, lat: float, lng: float):
        self.id = int(row['OPIS Truckstop ID'])
        self.name = row['Truckstop Name'].strip()
        self.address = row['Address'].strip()
        self.city = row['City'].strip()
        self.state = row['State'].strip()
        self.price = float(row['Retail Price'])
        self.lat = lat
        self.lng = lng

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'address': self.address,
            'city': self.city,
            'state': self.state,
            'price_per_gallon': round(self.price, 4),
            'lat': round(self.lat, 6),
            'lng': round(self.lng, 6),
        }


# In-memory store
_stations: list[FuelStation] = []
_stations_by_state: dict[str, list[FuelStation]] = {}
_load_lock = threading.Lock()
_loaded = False


def _assign_coords(city: str, state: str) -> tuple[float, float]:
    """
    Assign approximate coordinates to a city+state using state bounding boxes.
    We jitter within the state so stations don't all pile on the center.
    For the route algorithm this just needs to be "plausibly in the state".
    """
    if state not in STATE_BBOX:
        return (39.5, -98.35)  # US center fallback
    min_lat, max_lat, min_lng, max_lng = STATE_BBOX[state]
    # Use state center as the base
    center = STATE_CENTERS.get(state, ((min_lat + max_lat) / 2, (min_lng + max_lng) / 2))
    return center


def get_stations() -> list[FuelStation]:
    global _stations, _stations_by_state, _loaded
    if _loaded:
        return _stations
    with _load_lock:
        if _loaded:
            return _stations
        path = settings.FUEL_DATA_PATH
        seen: dict[int, FuelStation] = {}
        with open(path, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    state = row['State'].strip()
                    if state not in US_STATES:
                        continue  # skip Canadian stations
                    sid = int(row['OPIS Truckstop ID'])
                    price = float(row['Retail Price'])
                    lat, lng = _assign_coords(row['City'].strip(), state)
                    if sid in seen:
                        if price < seen[sid].price:
                            seen[sid].price = price
                    else:
                        seen[sid] = FuelStation(row, lat, lng)
                except (ValueError, KeyError):
                    continue

        _stations = list(seen.values())
        for s in _stations:
            _stations_by_state.setdefault(s.state, []).append(s)
        _loaded = True
    return _stations


def get_stations_by_state() -> dict[str, list[FuelStation]]:
    get_stations()
    return _stations_by_state


def cheapest_in_state(state: str) -> FuelStation | None:
    get_stations()
    candidates = _stations_by_state.get(state, [])
    if not candidates:
        return None
    return min(candidates, key=lambda s: s.price)


def cheapest_near_point(lat: float, lng: float, radius_miles: float = 100) -> FuelStation | None:
    """Find cheapest station within radius_miles of a point."""
    get_stations()
    best = None
    for s in _stations:
        if haversine(lat, lng, s.lat, s.lng) <= radius_miles:
            if best is None or s.price < best.price:
                best = s
    return best
