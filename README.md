# analytics-engine-c_car_platform

Python FastAPI analytics engine for the connected-car platform.

This MVP queries TimescaleDB (populated by `telematics-ingestion-c_car_platform`) and exposes a small reporting API for basic KPIs.

## What it queries (schema-aware)

`telematics-ingestion-c_car_platform` creates (defaults):

- schema: `telematics`
- table: `telemetry`
- columns (used by this service):
  - `time` (TIMESTAMPTZ, primary time column)
  - `vehicle_id` (TEXT)
  - `speed_kph` (DOUBLE PRECISION)

Configure these via environment variables (`TIMESCALE_SCHEMA`, `TIMESCALE_TABLE`) if they differ in your deployment.

## Non-blocking startup

The API starts even if TimescaleDB is down or misconfigured.

- `/health` always returns `ok: true` and includes DB readiness info.
- `/ready` reflects dependency readiness (TimescaleDB if enabled).
- Reporting endpoints return **503** when the DB is not ready.

## Environment

Copy and edit:

- `.env.example` -> `.env`

Minimum required for KPI queries:

- `TIMESCALE_DSN` (Postgres/Timescale connection string)
- `TIMESCALE_ENABLED=true`

## Install / run (local)

```bash
make install
python -m pip install -e .
python -m analytics_engine.main
```

Server defaults:

- `HOST=0.0.0.0`
- `PORT=3005`

API docs:

- `GET /docs`
- `GET /openapi.json`

## API

### Health

- `GET /health` (liveness + DB status)
- `GET /ready` (readiness)

### Reporting (KPIs)

Compute KPIs for a vehicle over a time window:

`GET /v1/reports/vehicle/{vehicleId}/kpis?start_time=...&end_time=...`

- `avg_speed_kph`: average of `speed_kph` samples
- `distance_km_est`: `avg_speed_kph * elapsed_hours` (MVP estimate)
- `utilization_pct`: `% of samples where speed_kph > 0`

Convenience endpoint for the last N minutes:

`GET /v1/reports/vehicle/{vehicleId}/kpis/last?minutes=60`

## Notes / future improvements

- Replace `distance_km_est` with odometer-based or segment-based integration when that data is available.
- Add fleet-level aggregations once fleet/vehicle ownership data is available.
