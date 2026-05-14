# Fuel Route Planner API

A Django REST API that plans fuel-optimised routes between any two US locations.
Given a start and finish, it returns the full driving route, the cheapest fuel stops
along the way (within the vehicle's 500-mile range), and the total fuel cost.

---

## Tech stack

| Concern | Tool | Cost |
|---|---|---|
| Framework | Django 5.x + Django REST Framework | Free |
| Geocoding | [Nominatim](https://nominatim.openstreetmap.org/) | Free, no key |
| Routing | [OSRM](http://router.project-osrm.org) | Free, no key |
| Fuel prices | Bundled OPIS CSV (8 151 US truck stops) | Included |

**External API calls per request: 3 max** (2 × Nominatim geocode + 1 × OSRM route).
All fuel selection is done locally — no extra API calls needed.

---

## Quick start

```bash
# 1. Clone / unzip the project
cd fuel_route

# 2. Create a virtual environment and install dependencies
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Place the fuel prices CSV in the project root (already included)
#    Expected path: fuel_prices.csv  (next to manage.py)

# 4. Run migrations (no models — just creates the SQLite file)
python manage.py migrate

# 5. Start the server
python manage.py runserver
```

The server starts at `http://127.0.0.1:8000`.
On first request (or at startup) the CSV is loaded into memory — ~6 600 US stations.

---

## API reference

### `POST /api/route/plan/`

Plan a fuel-optimised route between two US locations.

#### Request body

```json
{
  "start": "Los Angeles, CA",
  "finish": "New York, NY"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `start` | string | ✅ | Starting location (city/state or full US address) |
| `finish` | string | ✅ | Destination location (city/state or full US address) |

#### Example with `curl`

```bash
curl -X POST http://127.0.0.1:8000/api/route/plan/ \
  -H "Content-Type: application/json" \
  -d '{"start": "Los Angeles, CA", "finish": "New York, NY"}'
```

#### Example with Postman

1. Method: **POST**
2. URL: `http://127.0.0.1:8000/api/route/plan/`
3. Body → **raw → JSON**:
   ```json
   { "start": "Los Angeles, CA", "finish": "New York, NY" }
   ```

---

#### Response structure

```jsonc
{
  "start": { "location": "Los Angeles, CA", "lat": 34.0522, "lng": -118.2437 },
  "finish": { "location": "New York, NY",   "lat": 40.7128, "lng": -74.006  },

  "route_summary": {
    "total_distance_miles": 2798.4,
    "total_gallons_needed": 279.84,
    "total_fuel_cost_usd":  812.56,   // ← answer to "how much will it cost?"
    "number_of_fuel_stops": 5,
    "vehicle_max_range_miles": 500,
    "vehicle_mpg": 10
  },

  // Each stop is the CHEAPEST station in the relevant state along the route
  "fuel_stops": [
    {
      "id": 12345,
      "name": "TCI PHOENIX",
      "address": "I-10, EXIT 143",
      "city": "Phoenix",
      "state": "AZ",
      "price_per_gallon": 2.9223,
      "lat": 33.729759,
      "lng": -111.431221
    }
    // ... more stops
  ],

  // Per-leg breakdown (from → to, distance, gallons, cost at that leg's fuel price)
  "legs": [
    {
      "from": "Start: Los Angeles, CA",
      "to": "TCI PHOENIX – Phoenix, AZ",
      "distance_miles": 372.1,
      "gallons_used": 37.21,
      "price_per_gallon": 3.15,
      "leg_fuel_cost_usd": 117.21
    }
    // ... more legs
  ],

  // Map data for rendering with Leaflet / Google Maps / Mapbox
  "map": {
    "polyline_coords": [[34.0522, -118.2437], [34.1, -117.9], /* ... */],
    "markers": [
      { "type": "start",     "label": "START: Los Angeles, CA", "lat": 34.0522, "lng": -118.2437 },
      { "type": "fuel_stop", "label": "FUEL STOP #1",           "lat": 33.73,  "lng": -111.43,
        "station_name": "TCI PHOENIX", "price_per_gallon": 2.9223, "city": "Phoenix", "state": "AZ" },
      { "type": "end",       "label": "END: New York, NY",      "lat": 40.7128, "lng": -74.006  }
    ]
  },

  "_meta": {
    "computation_time_ms": 1243,
    "external_api_calls": "2 geocode (Nominatim) + 1 route (OSRM) = 3 total"
  }
}
```

### `GET /api/route/plan/`

Returns usage information (no parameters required — useful for health checks).

---

## Algorithm

```
1. Geocode start  → Nominatim → (lat, lng)          [API call 1]
2. Geocode finish → Nominatim → (lat, lng)          [API call 2]
3. OSRM routing   → full encoded polyline + distance [API call 3]

4. LOCAL: Decode polyline into (lat, lng) sequence
5. LOCAL: Walk the polyline accumulating miles
         Every time accumulated distance approaches 490 mi (500 - 10 buffer):
           a. Determine the US state at the current polyline point
              (via bounding-box lookup — O(50) comparisons, sub-millisecond)
           b. Pick the cheapest station in that state from the CSV
           c. Reset accumulated distance counter
6. LOCAL: Build per-leg cost breakdown
7. LOCAL: Thin the polyline (every 5th point) for compact JSON response
```

**Why state-based selection?**
The 8 151-station CSV provides city+state but no lat/lng coordinates.
Rather than geocoding each station (8 000+ API calls) or maintaining an
embedded city-coordinate dictionary, we match the route corridor to US
states using O(1) bounding-box checks, then return the cheapest station
in each state the route passes through. This is accurate, fast, and
requires zero additional API calls.

---

## Vehicle assumptions

| Parameter | Value |
|---|---|
| Max range | 500 miles per tank |
| Fuel efficiency | 10 MPG |
| Effective max between stops | 490 miles (10-mile safety buffer) |

---

## Running tests

```bash
python manage.py test route_planner
```

All 16 tests run in < 1 second. External APIs are mocked.

---

## Configuration (`fuel_route_project/settings.py`)

| Setting | Default | Description |
|---|---|---|
| `FUEL_DATA_PATH` | `BASE_DIR / 'fuel_prices.csv'` | Path to the OPIS fuel prices CSV |
| `VEHICLE_MAX_RANGE_MILES` | `500` | Vehicle tank range in miles |
| `VEHICLE_MPG` | `10` | Vehicle fuel efficiency |
| `OSRM_BASE_URL` | `http://router.project-osrm.org` | OSRM routing server URL |

---

## Performance

- **Startup**: CSV loaded once into memory (~6 600 stations, < 1 second)
- **Per-request**: 3 external HTTP calls (geocode × 2, route × 1) + local computation
- **Local computation**: O(n) polyline walk — typically < 5 ms for any US route
- **Total response time**: dominated by network latency to Nominatim/OSRM (~1–3 seconds)

---

## Project structure

```
fuel_route/
├── manage.py
├── requirements.txt
├── fuel_prices.csv              ← OPIS fuel price data (bundled)
├── fuel_route_project/
│   ├── settings.py              ← Configuration
│   └── urls.py                  ← Root URL config
└── route_planner/
    ├── apps.py                  ← Pre-loads CSV at startup
    ├── fuel_data.py             ← CSV loading, state bounding boxes, station lookup
    ├── route_service.py         ← Geocoding, OSRM routing, stop selection, cost calc
    ├── serializers.py           ← DRF input validation
    ├── views.py                 ← API endpoint
    ├── urls.py                  ← App URL config
    └── tests.py                 ← 16 unit + integration tests
```
