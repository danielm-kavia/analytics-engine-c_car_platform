from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from analytics_engine.config import Settings
from analytics_engine.db import TimescaleClient


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class KpiResponse(BaseModel):
    """KPI response for a vehicle within a time window."""

    vehicle_id: str = Field(..., description="Vehicle identifier (VIN or platform vehicle id).")
    start_time: datetime = Field(..., description="Start of the reporting window (inclusive).")
    end_time: datetime = Field(..., description="End of the reporting window (exclusive).")

    avg_speed_kph: Optional[float] = Field(
        default=None, description="Average speed in kph (computed from speed_kph samples)."
    )
    distance_km_est: Optional[float] = Field(
        default=None,
        description=(
            "Estimated distance in km, computed from avg speed and elapsed time (sample-based). "
            "MVP metric; replace with odometer/segment integration when available."
        ),
    )
    utilization_pct: Optional[float] = Field(
        default=None,
        description="Percent of samples where speed_kph > 0 within the window (0..100).",
    )

    samples: int = Field(..., description="Number of telemetry rows in the window.")
    moving_samples: int = Field(..., description="Number of rows with speed_kph > 0 in the window.")

    data_source: str = Field(
        default="timescale",
        description="Data source for the KPIs (timescale or unavailable).",
    )


class KpiService:
    """Service for computing KPI metrics from TimescaleDB telemetry table."""

    def __init__(self, settings: Settings, db: TimescaleClient) -> None:
        self._settings = settings
        self._db = db

    async def compute_vehicle_kpis(
        self, vehicle_id: str, start_time: datetime, end_time: datetime
    ) -> KpiResponse:
        """Compute KPIs for a vehicle and window.

        Notes on schema alignment:
        telematics-ingestion creates:
          schema: telematics (default)
          table: telemetry (default)
          columns: time, device_id, vehicle_id, speed_kph, latitude, longitude, ...
        """
        schema = self._settings.timescale_schema
        table = self._settings.timescale_table

        # Aggregate in one query for efficiency and to keep API latency low.
        row = await self._db.fetchrow(
            f"""
            SELECT
              COUNT(*)::int AS samples,
              COUNT(*) FILTER (WHERE speed_kph IS NOT NULL)::int AS speed_samples,
              COUNT(*) FILTER (WHERE COALESCE(speed_kph, 0) > 0)::int AS moving_samples,
              AVG(speed_kph) AS avg_speed_kph
            FROM "{schema}"."{table}"
            WHERE vehicle_id = $1
              AND time >= $2
              AND time < $3;
            """,
            vehicle_id,
            start_time,
            end_time,
        )

        samples = int(row["samples"] or 0)
        moving_samples = int(row["moving_samples"] or 0)
        avg_speed_kph = row["avg_speed_kph"]
        avg_speed_kph_val = float(avg_speed_kph) if avg_speed_kph is not None else None

        window_s = max(0.0, (end_time - start_time).total_seconds())
        distance_km_est = None
        if avg_speed_kph_val is not None:
            distance_km_est = avg_speed_kph_val * (window_s / 3600.0)

        utilization_pct = None
        if samples > 0:
            utilization_pct = (moving_samples / samples) * 100.0

        return KpiResponse(
            vehicle_id=vehicle_id,
            start_time=start_time,
            end_time=end_time,
            avg_speed_kph=avg_speed_kph_val,
            distance_km_est=distance_km_est,
            utilization_pct=utilization_pct,
            samples=samples,
            moving_samples=moving_samples,
        )

    async def compute_vehicle_kpis_last_minutes(self, vehicle_id: str, minutes: int) -> KpiResponse:
        """Convenience wrapper using last N minutes."""
        end_time = _utc_now()
        start_time = end_time - __import__("datetime").timedelta(minutes=minutes)
        return await self.compute_vehicle_kpis(vehicle_id=vehicle_id, start_time=start_time, end_time=end_time)
