from __future__ import annotations

import os

import uvicorn

from analytics_engine.app import create_app
from analytics_engine.config import get_settings


# PUBLIC_INTERFACE
def main() -> None:
    """Run the analytics engine API using Uvicorn.

    Environment:
      - HOST, PORT control bind
      - UVICORN_WORKERS can be used in production (default 1)
    """
    settings = get_settings()
    workers = int(os.getenv("UVICORN_WORKERS", "1"))

    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        workers=workers,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
