# Fuel Route API

A Django REST API that takes a start and a finish location in the USA and returns the driving route, the
cheapest places to buy fuel along it (500-mile range, 10 mpg), and the total fuel money spent. Fuel prices
come from a truck-stop price CSV that has no coordinates, so the project cleans the file, geocodes every
station **offline** from bundled GeoNames data, and then answers each request with **one** call to the
OpenRouteService (ORS) directions API plus a few milliseconds of in-memory work. Repeat requests are served
from a cache with zero external calls. A small Leaflet map page (`/map/`) shows the result.

## Quick start

You need Python 3.12 or newer. An [OpenRouteService API key](https://openrouteservice.org/dev/#/signup) (free)
is only needed for `POST /api/route/`; the data commands, tests and the map page load without one.

**Windows (PowerShell)**

```powershell
git clone <repo-url> fuel-route-api
cd fuel-route-api
py -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope Process RemoteSigned
python -m pip install -r requirements.txt
Copy-Item .env.example .env           # then edit .env and set ORS_API_KEY=...
python manage.py migrate
python manage.py load_stations        # clean the CSV -> 6,626 stations
python manage.py geocode_stations     # give every station lat/lng (offline, a few seconds)
python manage.py runserver
pytest                                # in a second terminal with the venv active
```

**macOS / Linux**

```bash
git clone <repo-url> fuel-route-api
cd fuel-route-api
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env                  # then edit .env and set ORS_API_KEY=...
python manage.py migrate
python manage.py load_stations
python manage.py geocode_stations
python manage.py runserver
pytest
```

Then open <http://127.0.0.1:8000/map/> (`/` redirects there), or call the API:

```bash
curl -s -X POST http://127.0.0.1:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d '{"start": "Chicago, IL", "finish": "Los Angeles, CA"}'
```

On Windows PowerShell 5.1, `curl` is an alias for `Invoke-WebRequest` and quoting JSON for `curl.exe` is
unreliable, so use `Invoke-RestMethod`:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/route/ -ContentType "application/json" `
  -Body '{"start": "Chicago, IL", "finish": "Los Angeles, CA"}' | ConvertTo-Json -Depth 8
```

or `curl.exe` with a body file (this also lets you see error bodies, which `Invoke-RestMethod` hides behind an
exception):

```powershell
Set-Content -Path body.json -Value '{"start": "Chicago, IL", "finish": "Los Angeles, CA"}' -Encoding ascii
curl.exe -s -X POST http://127.0.0.1:8000/api/route/ -H "Content-Type: application/json" --data-binary "@body.json"
```

Notes: `.env` may be UTF-8 or the UTF-16 that PowerShell's `>` redirect writes; both load, and a real
`ORS_API_KEY` environment variable takes precedence over `.env`. Without an ORS key the API answers
`502 routing_service_error` ("ORS_API_KEY is not configured"). `geocode_stations` uses the committed
`routing/data/us_places.csv`; add `--refresh` to rebuild it from GeoNames (needs network).

### Example response (real Chicago → Los Angeles request, trimmed)

```json
{
  "start":  {"name": "Chicago, IL",     "lat": 41.85421, "lng": -87.67605},
  "finish": {"name": "Los Angeles, CA", "lat": 34.03372, "lng": -118.28135},
  "distance_miles": 2017.82,
  "route": {"type": "LineString", "coordinates": [[-87.6759, 41.85421], [-87.67651, 41.87622], "... 1,155 points"]},
  "fuel_stops": [
    {"order": 1, "name": "KUM & GO #0370", "city": "Gretna", "state": "NE", "lat": 41.1345, "lng": -96.2458,
     "price_per_gallon": 2.9207, "mile_marker": 482.1, "gallons_purchased": 5.63, "cost": 16.44},
    {"order": 2, "name": "AKAL TRAVEL CENTER", "city": "Waco", "state": "NE", "lat": 40.9198, "lng": -97.4534,
     "price_per_gallon": 2.799, "mile_marker": 556.3, "gallons_purchased": 50.0, "cost": 139.95},
    "... 5 more stops"
  ],
  "total_gallons_purchased": 151.78,
  "total_fuel_cost": 462.19,
  "assumptions": {"range_miles": 500, "mpg": 10, "start_tank": "full, not charged", "corridor_miles": 10,
                  "stop_penalty_usd": 2.0, "price_rule": "lowest price per station"},
  "meta": {"external_api_calls": 1, "cached": false, "compute_ms": 32.9, "route_call_ms": 1548.0,
           "timings_ms": {"place_lookup": 0.6, "route_call": 1548.0, "station_match": 16.0,
                          "planning": 9.1, "route_simplify": 6.4, "total_compute": 32.9}}
}
```

Sending the same request again returns the same body with `meta.cached: true`, `external_api_calls: 0` and
`compute_ms` well under a millisecond.

## Endpoint reference

| Endpoint | Purpose |
|---|---|
| `POST /api/route/` | Plan a trip (below). JSON in, JSON out, no authentication or CSRF token. |
| `GET /api/health/` | Liveness probe: `{"status": "ok"}`. |
| `GET /map/` | Single-page Leaflet UI that calls `POST /api/route/`. `/` redirects here. |

### `POST /api/route/`

**Request** (`Content-Type: application/json`)

| Field | Type | Notes |
|---|---|---|
| `start` | string, max 100 chars | `"City, ST"` (US state code, e.g. `"Denver, CO"`) or `"lat,lng"`. |
| `finish` | string, max 100 chars | Same formats. Must differ from `start`. |

City names are matched case-insensitively with punctuation ignored and `Ft.`/`St.`/`Mt.` expanded.

**Response fields**

| Field | Meaning |
|---|---|
| `start`, `finish` | `name` (canonical), `lat`, `lng` of the resolved places. |
| `distance_miles` | Driving distance reported by ORS. |
| `route` | GeoJSON `LineString`, coordinates in `[lng, lat]` order, simplified to at most 1,500 points. |
| `fuel_stops[]` | Stops where fuel is bought, in route order: `order`, `name`, `city`, `state`, `lat`, `lng`, `price_per_gallon`, `mile_marker` (miles from the start), `gallons_purchased`, `cost`. |
| `total_gallons_purchased`, `total_fuel_cost` | Fuel bought at stops and what it costs. The initial full tank is not charged. |
| `assumptions` | The constants used: `range_miles`, `mpg`, `start_tank`, `corridor_miles`, `stop_penalty_usd`, `price_rule`. |
| `meta` | `external_api_calls` (1, or 0 on a cache hit), `cached`, `compute_ms` (our own work, ORS excluded), `route_call_ms`, and a `timings_ms` breakdown: `place_lookup`, `route_call`, `station_match`, `planning`, `route_simplify`, `total_compute`. |

**Errors** are always JSON, never a stack trace: `{"error": {"code": "...", "message": "..."}}`.

| HTTP | `code` | When |
|---|---|---|
| 400 | `invalid_input` | Missing/blank/too-long field, malformed JSON. |
| 400 | `invalid_place` | Unparseable or unknown/non-US place, or coordinates out of range. |
| 400 | `same_location` | `start` and `finish` resolve to the same point. |
| 404 | `no_route_found` | ORS found no drivable route (for example an unroutable point). |
| 405 / 415 | `method_not_allowed` / `unsupported_media_type` | Wrong method or a non-JSON body. |
| 422 | `no_feasible_route` | A gap between fuel points exceeds the 500-mile range. The message says where the gap is. |
| 500 | `internal_error` | Unexpected error (details are logged server-side only). |
| 502 | `routing_service_error` | ORS failed, timed out, rate-limited, rejected the key, or the key is not configured. |

## Architecture

```
 browser (/map/)  or  curl
          |
          v
   POST /api/route/   -> RouteView + serializer: validate JSON              (views.py)
          |
          v
   trip.plan_trip(start, finish)                                            (services/trip.py)
          |
          |-- 1. geocode.lookup_place x2      local city index built from routing/data/us_places.csv, no network
          |-- 2. cache lookup                 key = resolved coords + corridor + penalty      --hit--> return (0 external calls)
          |-- 3. route_client.get_route  ====> OpenRouteService     *** the ONE external call, cache misses only ***
          |-- 4. stations.candidates_along_route    cached numpy station arrays + scipy cKDTree over route vertices
          |-- 5. planner.plan_fuel_stops      pure Python: greedy + stop elimination (no Django, no network, no DB)
          |-- 6. build payload, simplify route, cache it for 24 h
          v
   JSON response

 Offline data pipeline (run once):
   data/fuel-prices-for-be-assessment.csv --load_stations--> SQLite FuelStation rows (cleaned, no coordinates)
   routing/data/us_places.csv (GeoNames zip centroids per city) --geocode_stations--> lat/lng on every station
```

Layout: `routing/services/` holds the business logic as plain functions (`cleaning`, `geocode`, `route_client`,
`stations`, `planner`, `trip`); `views.py`/`serializers.py` are thin; `routing/tests/` has unit, API and
regression tests.

## How the planner works

`planner.plan_fuel_stops(total_miles, candidates, range_miles=500, mpg=10, stop_penalty=2.0)` is a pure function.
Candidates are stations within the corridor, each with a `mile_marker` (distance along the route) and a price. The
tank holds `range_miles / mpg` = 50 gallons and **starts full; that fuel is free**. At each station the greedy
either (a) buys just enough to reach the first cheaper station within a full tank, or (b) if nothing cheaper is
in range, fills the tank and drives to the cheapest station in range. The destination counts as a free station,
so the last stop buys only what is needed.

**Worked example.** 800 miles; station A at mile 400 costs $3.50, B at mile 600 costs $3.00. The free 50 gallons
reach A with 10 gallons left. B is cheaper and 20 gallons away, so buy 10 gallons at A (10 × $3.50 = $35.00).
Arrive at B empty and buy the 20 gallons the last 200 miles need (20 × $3.00 = $60.00). Total: 30 gallons, $95.00
(80 gallons burned minus 50 free).

**Stop penalty.** The greedy will happily buy 1.8 gallons at a station 18 miles after a full fill to save a few
cents. A stop is worth making only if it saves at least `stop_penalty` dollars (`FUEL_STOP_PENALTY`, default
$2.00). After the greedy, the planner removes stops backwards: a removal is adopted only if re-planning without
those stations is feasible, **saves at least one stop**, and adds less than `stop_penalty × stops saved` in fuel
cost. Single stops are tried first; only if none qualifies are two neighbouring stops dropped together. Example:
1,100 miles with stations at mile 450 ($3.00), 468 ($3.10) and 900 ($3.30). The pure greedy makes three stops for
$184.14, including a 1.8-gallon top-up at mile 468; dropping that stop costs $0.36 more, so the planner returns two
stops for $184.50. The penalty is a decision rule only: reported totals are always the real fuel cost.
`stop_penalty=0` gives the pure greedy, which is checked against an exact DP in the tests.

Stop elimination is a **heuristic** (local search). It can miss a better plan that would need a substitute
station excluded too; on the real Chicago → Los Angeles route an exact solver finds a 6-stop plan costing $1.28
more than the 7-stop plan returned.

## Performance

Measured on the development machine (Windows 11, Python 3.12) with a real ORS Chicago → Los Angeles route (14,197
vertices) and a New York → Dallas route (12,047 vertices), fresh processes, ORS call excluded unless stated:

| Case | Time |
|---|---|
| **ORS route call** (one real request) | **about 1.5-1.8 s** (1,548, 1,738 and 1,806 ms in three real calls) |
| First request after a cold start, **with** startup warm-up (the default under `runserver`/WSGI/ASGI) | 26-57 ms |
| First request after a cold start, without warm-up | 210-285 ms (loading the city index and station arrays) |
| Steady state, cache miss (median of 20) | about 21-23 ms: station match ~8, planning ~8-9, simplify ~5 |
| Cache hit | under 1 ms of compute, ~8 ms end to end |
| Startup warm-up (once) | 145-185 ms |

So **ORS latency dominates**: on a miss the whole request is roughly 1.5-1.8 s of waiting for ORS plus ~30-60 ms of
our work; a repeat is milliseconds (about 10 ms end to end through the dev server). A 3,000-vertex route against all 6,626 stations matches in about 5 ms. The
optimizations (precomputed city keys, vectorized simplification, float price load, a shared route array) are
locked in by golden regression tests that prove results are unchanged.

**Caching.** The finished response payload (not the route) is cached for 24 hours in Django's local-memory cache,
keyed on the *resolved coordinates* plus the corridor and stop-penalty settings, so `"chicago, il"` and
`"Chicago, IL "` share an entry. Errors are not cached.

## Assumptions and trade-offs

- **Full starting tank, not charged.** The trip starts with 50 gallons that are free; only fuel bought at stops is
  charged. Range 500 miles, 10 mpg, tank 50 gallons.
- **City-level geocoding, hence a 10-mile corridor.** The price CSV has no coordinates. Stations are placed at the
  average of the GeoNames zip centroids for their city, and start/finish are city centroids (ORS snaps them to the
  nearest road). A station's real position can be several miles from its city centroid, so stations within
  `STATION_CORRIDOR_MILES = 10` of the route count, not 5. A station is tied to its nearest route vertex, which
  sets its mile marker; no detour distance is computed.
- **Lowest price per station.** The CSV repeats station IDs with different prices (and names); each ID is collapsed
  to one row with its lowest price and first non-empty text fields. If two stations land at the same mile marker the
  cheaper one is kept.
- **Canadian rows dropped.** 620 of the 8,151 rows have a non-US province (AB, BC, MB, NB, NS, ON, QC, SK, YT). The
  50 states plus DC remain: 6,626 stations.
- **Stop penalty $2.00** (`FUEL_STOP_PENALTY`) to avoid pointless micro-stops; it can make the plan slightly more
  expensive in exchange for fewer stops (see above).
- **24-hour local-memory cache (single process).** Each worker process has its own cache, so with several workers a
  repeat request can miss. A cached plan can be up to 24 h stale after station data changes.
- **Station data is loaded at startup.** The city index and station arrays are read once (warm-up in
  `wsgi.py`/`asgi.py`). After re-running `load_stations` or `geocode_stations`, **restart the server** (or call
  `invalidate_station_cache()` in that process).
- **Route simplified for output.** The returned line has at most about 1,500 points (Douglas-Peucker, endpoints
  kept); matching uses the full geometry.
- **Other limits.** Stops listed are only where fuel is bought; `lat,lng` input is range-checked but not required to
  be inside the US; routing failures (404/502) and infeasible trips (422) are not cached.

## Tests

`pytest` runs the suite offline (143 tests, about 15-20 s; the ORS call is always mocked). It includes exact
hand-computed planner cases, an exact DP oracle, seeded random invariant tests, and **golden regression tests**
(`routing/tests/test_regression_golden.py`) built from two real ORS routes saved in `routing/tests/fixtures/`.
If you change planner or matching behaviour on purpose, regenerate the golden file deliberately with
`UPDATE_GOLDEN=1 pytest routing/tests/test_regression_golden.py` and review the diff.

## Deploying

Development defaults work out of the box. For production set `DJANGO_DEBUG=False`, `DJANGO_SECRET_KEY` (the app
refuses to start without it) and `DJANGO_ALLOWED_HOSTS`; this turns on HTTPS redirect, secure cookies and a
one-hour HSTS (`DJANGO_HSTS_SECONDS` to raise it, `DJANGO_TRUST_PROXY_SSL=1` behind a TLS-terminating proxy). With
those set, `python manage.py check --deploy` reports only `security.W005` and `W021` (HSTS subdomains/preload),
which are deliberately left off because they are only safe on a domain you fully own. Serve with a real WSGI/ASGI
server, not `runserver`.

## What I would do with more time

- **PostGIS** for spatial matching (corridor queries and nearest-point-on-line instead of nearest vertex).
- **Exit-level geocoding from the Address column** (`I-44, EXIT 283`) to replace city-centroid positions, which
  would let the corridor shrink back to a few miles.
- **A Redis cache** shared across workers, with explicit invalidation when prices are reloaded.
- **Real-time prices** instead of a static CSV snapshot.
- **Detour-aware stop selection**: weigh the extra driving to leave the route against the price saved.
- An exact (DP/MILP) planner for the stop-penalty objective instead of the local-search heuristic.

## AI assistance

This project was built with AI coding assistance (Claude Code). I reviewed every part of the code and understand
how it works, including the planner, the geocoding and matching, and the tests.
