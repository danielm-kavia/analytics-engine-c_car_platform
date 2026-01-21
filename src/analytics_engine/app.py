from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import os

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from analytics_engine.config import Settings, get_settings
from analytics_engine.db import TimescaleClient
from analytics_engine.kpis import KpiResponse, KpiService

logger = logging.getLogger("analytics-engine")


class HealthResponse(BaseModel):
    """Health response model."""

    ok: bool = Field(..., description="Whether the service process is running.")
    service: str = Field(..., description="Service name.")
    db: dict = Field(..., description="Database status summary.")


def _build_app(settings: Settings) -> FastAPI:
    app = FastAPI(
        title="Analytics Engine API",
        description=(
            "MVP analytics service for connected-car platform. "
            "Queries TimescaleDB (telemetry hypertable) for basic KPIs."
        ),
        version="0.1.0",
        openapi_tags=[
            {"name": "health", "description": "Health and readiness endpoints."},
            {"name": "reports", "description": "Reporting endpoints for KPIs."},
        ],
    )

    # Hardening (Phase 9): set basic security headers.
    # Defaults are non-breaking for API-only services.
    @app.middleware("http")
    async def security_headers(request: Request, call_next) -> Response:
        response = await call_next(request)

        # Baselines
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-DNS-Prefetch-Control"] = "off"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=(), payment=(), usb=(), interest-cohort=()"
        )
        response.headers["Cross-Origin-Resource-Policy"] = os.getenv(
            "CROSS_ORIGIN_RESOURCE_POLICY", "same-origin"
        )

        # Optional CSP (disabled by default to avoid surprises if HTML is ever served).
        if os.getenv("SECURITY_ENABLE_CSP", "false").lower() == "true":
            response.headers["Content-Security-Policy"] = os.getenv(
                "SECURITY_CSP", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            )

        # Optional HSTS (only enable when behind HTTPS).
        if os.getenv("SECURITY_ENABLE_HSTS", "false").lower() == "true":
            response.headers["Strict-Transport-Security"] = os.getenv(
                "SECURITY_HSTS", "max-age=15552000; includeSubDomains"
            )

        return response

    # CORS is optional but helpful for previews/dashboards.
    origins = settings.cors_origins()
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
            max_age=3600,
        )

    db_client = TimescaleClient(settings=settings, logger=logger)
    kpi_service = KpiService(settings=settings, db=db_client)

    app.state.settings = settings
    app.state.db = db_client
    app.state.kpis = kpi_service

    @app.on_event("startup")
    async def _startup() -> None:
        # Non-blocking startup: background retry loop; API comes up regardless.
        await db_client.start_background()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await db_client.stop()

    def get_db() -> TimescaleClient:
        return app.state.db

    def get_kpis() -> KpiService:
        return app.state.kpis

    @app.get("/health", tags=["health"], summary="Liveness check", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """PUBLIC_INTERFACE
        Liveness endpoint: indicates the service process is running.
        """
        st = db_client.status()
        return HealthResponse(
            ok=True,
            service=settings.service_name,
            db={"enabled": st.enabled, "ready": st.ready, "error": st.error},
        )

    @app.get("/ready", tags=["health"], summary="Readiness check")
    async def ready() -> dict:
        """PUBLIC_INTERFACE
        Readiness endpoint: indicates whether required dependencies are ready.

        For MVP, readiness is based on TimescaleDB connectivity when enabled.
        """
        st = db_client.status()
        if not st.enabled:
            return {"ok": True, "ready": True, "dependencies": {"timescale": {"enabled": False}}}
        return {
            "ok": True,
            "ready": bool(st.ready),
            "dependencies": {"timescale": {"enabled": True, "ready": st.ready, "error": st.error}},
        }

    @app.get(
        "/v1/reports/vehicle/{vehicle_id}/kpis",
        tags=["reports"],
        summary="Vehicle KPI report",
        response_model=KpiResponse,
    )
    async def vehicle_kpis(
        vehicle_id: str,
        start_time: datetime = Query(..., description="Window start time (ISO-8601)."),
        end_time: datetime = Query(..., description="Window end time (ISO-8601)."),
        db: TimescaleClient = Depends(get_db),
        svc: KpiService = Depends(get_kpis),
    ) -> KpiResponse:
        """PUBLIC_INTERFACE
        Compute basic KPIs for a vehicle over a requested time window.

        KPIs (MVP):
        - avg_speed_kph: AVG(speed_kph) over samples
        - distance_km_est: avg_speed_kph * elapsed_hours
        - utilization_pct: % samples with speed_kph > 0
        """
        if not settings.timescale_enabled:
            raise HTTPException(status_code=503, detail="TimescaleDB disabled")
        if not db.is_ready():
            raise HTTPException(status_code=503, detail="TimescaleDB not ready")

        if end_time <= start_time:
            raise HTTPException(status_code=400, detail="end_time must be after start_time")

        return await svc.compute_vehicle_kpis(vehicle_id=vehicle_id, start_time=start_time, end_time=end_time)

    @app.get(
        "/v1/reports/vehicle/{vehicle_id}/kpis/last",
        tags=["reports"],
        summary="Vehicle KPI report (last N minutes)",
        response_model=KpiResponse,
    )
    async def vehicle_kpis_last(
        vehicle_id: str,
        minutes: int = Query(60, ge=1, le=7 * 24 * 60, description="Number of minutes to look back."),
        db: TimescaleClient = Depends(get_db),
        svc: KpiService = Depends(get_kpis),
    ) -> KpiResponse:
        """PUBLIC_INTERFACE
        Convenience endpoint computing KPIs for the last N minutes.
        """
        if not settings.timescale_enabled:
            raise HTTPException(status_code=503, detail="TimescaleDB disabled")
        if not db.is_ready():
            raise HTTPException(status_code=503, detail="TimescaleDB not ready")
        return await svc.compute_vehicle_kpis_last_minutes(vehicle_id=vehicle_id, minutes=minutes)

    return app


# PUBLIC_INTERFACE
def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """Create and return the FastAPI app instance."""
    st = settings or get_settings()

    # Basic logging config suitable for container logs.
    logging.basicConfig(level=getattr(logging, st.log_level.upper(), logging.INFO))
    return _build_app(st)


app = create_app()
