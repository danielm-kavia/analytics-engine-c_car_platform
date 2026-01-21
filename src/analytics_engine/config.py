from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    The service is designed to start even if the database is unavailable. DB connectivity
    is checked lazily and via health endpoints.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_name: str = Field(default="analytics-engine", description="Service name for logs/metadata.")
    environment: str = Field(default="development", description="Runtime environment name.")
    log_level: str = Field(default="INFO", description="Log verbosity (e.g., INFO, DEBUG).")

    host: str = Field(default="0.0.0.0", description="Bind host for the HTTP server.")
    port: int = Field(default=3005, description="Bind port for the HTTP server.")

    # Timescale / Postgres connection
    timescale_enabled: bool = Field(
        default=True, description="Enable TimescaleDB integration. If false, KPIs return unavailable."
    )
    timescale_dsn: Optional[str] = Field(
        default=None,
        description=(
            "PostgreSQL connection string, e.g. "
            "postgresql://user:pass@host:5432/dbname (TimescaleDB compatible)."
        ),
    )
    timescale_schema: str = Field(
        default="telematics",
        description="DB schema used by telematics-ingestion service.",
    )
    timescale_table: str = Field(
        default="telemetry",
        description="DB table used by telematics-ingestion service.",
    )

    # CORS - keep minimal but configurable (optional use by preview)
    allowed_origins: List[str] = Field(
        default_factory=list,
        description="Comma-separated list of allowed CORS origins.",
        validation_alias="ALLOWED_ORIGINS",
    )

    request_timeout_ms: int = Field(
        default=30_000, description="Default query timeout budget (client-side) in milliseconds."
    )

    def cors_origins(self) -> List[str]:
        """Return normalized CORS origin list."""
        # Accept both List[str] (pydantic) and legacy comma-separated string (env)
        if isinstance(self.allowed_origins, list):
            return [o.strip() for o in self.allowed_origins if o and o.strip()]
        return []


@lru_cache(maxsize=1)
# PUBLIC_INTERFACE
def get_settings() -> Settings:
    """Get cached Settings instance."""
    return Settings()
