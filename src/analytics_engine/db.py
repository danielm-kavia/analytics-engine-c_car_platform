from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

import asyncpg

from analytics_engine.config import Settings


@dataclass(frozen=True)
class DbStatus:
    """Represents DB status for health endpoints."""

    enabled: bool
    ready: bool
    error: Optional[str]


class TimescaleClient:
    """Async TimescaleDB client wrapper.

    - Does not connect on import.
    - Startup is non-blocking: we try to connect in background and keep retrying.
    - Queries are guarded: if not ready, raise a controlled RuntimeError.
    """

    def __init__(self, settings: Settings, logger: logging.Logger) -> None:
        self._settings = settings
        self._logger = logger

        self._pool: Optional[asyncpg.Pool] = None
        self._last_error: Optional[str] = None

        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task[None]] = None

    def is_ready(self) -> bool:
        """Return True if a connection pool is available."""
        return self._pool is not None

    def status(self) -> DbStatus:
        """Return DB status information."""
        return DbStatus(
            enabled=bool(self._settings.timescale_enabled),
            ready=self.is_ready() if self._settings.timescale_enabled else False,
            error=self._last_error,
        )

    async def start_background(self) -> None:
        """Start background connection/retry loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._retry_loop(), name="timescale-retry-loop")

    async def stop(self) -> None:
        """Stop background loop and close pool."""
        self._stop_event.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except Exception:
                pass

        if self._pool:
            pool = self._pool
            self._pool = None
            try:
                await pool.close()
            except Exception as e:
                self._logger.warning("Failed to close Timescale pool: %s", e)

    async def _connect_once(self) -> None:
        if not self._settings.timescale_enabled:
            self._pool = None
            self._last_error = None
            return
        if not self._settings.timescale_dsn:
            self._pool = None
            self._last_error = "TIMESCALE_DSN not set"
            return
        if self._pool is not None:
            return

        self._logger.info("Connecting to TimescaleDB...")
        try:
            # Use a small pool; analytics engine MVP is low-throughput.
            self._pool = await asyncpg.create_pool(
                dsn=self._settings.timescale_dsn,
                min_size=1,
                max_size=5,
                command_timeout=max(1, int(self._settings.request_timeout_ms / 1000)),
            )
            self._last_error = None
            self._logger.info("TimescaleDB connected (pool ready)")
        except Exception as e:
            self._pool = None
            self._last_error = str(e)
            raise

    async def _retry_loop(self) -> None:
        # Exponential backoff with cap.
        delay_s = 1.0
        max_delay_s = 30.0

        while not self._stop_event.is_set():
            try:
                await self._connect_once()
                # If connected (or disabled/misconfigured), wait a bit and check again.
                delay_s = 1.0
                await asyncio.wait_for(self._stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                self._logger.warning("TimescaleDB not ready; retrying soon: %s", e)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay_s)
                except asyncio.TimeoutError:
                    pass
                delay_s = min(max_delay_s, delay_s * 1.6)

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record:
        """Fetch a single row from the database."""
        if not self._settings.timescale_enabled:
            raise RuntimeError("TimescaleDB disabled")
        if not self._pool:
            raise RuntimeError("TimescaleDB not ready")
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(query, *args)  # type: ignore[no-any-return]

    async def fetchval(self, query: str, *args: Any) -> Any:
        """Fetch a single scalar value from the database."""
        if not self._settings.timescale_enabled:
            raise RuntimeError("TimescaleDB disabled")
        if not self._pool:
            raise RuntimeError("TimescaleDB not ready")
        async with self._pool.acquire() as conn:
            return await conn.fetchval(query, *args)
