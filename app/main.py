"""Application factory and process-wide lifespan.

Run locally with ``uv run fastapi dev`` (entrypoint configured in
``pyproject.toml``) or in production with ``uv run fastapi run``.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from app.api.router import api_v1_router
from app.core.config import get_settings
from app.core.database import create_database_engine, create_session_factory
from app.core.exceptions import register_exception_handlers
from app.core.http_client import create_http_client
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.features.health.router import router as health_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[dict[str, Any]]:
    """Create shared resources on startup and release them on shutdown.

    The yielded mapping becomes lifespan state: Starlette copies it
    onto ``request.state`` for every request, which is how the
    dependencies in :mod:`app.core` reach the engine and HTTP client.
    """
    settings = get_settings()
    db_engine = create_database_engine(settings)
    http_client = create_http_client(settings)
    logger.info(
        "Starting %s %s in %s",
        settings.service_name,
        settings.service_version,
        settings.environment,
    )
    try:
        yield {
            "db_engine": db_engine,
            "session_factory": create_session_factory(db_engine),
            "http_client": http_client,
        }
    finally:
        await http_client.aclose()
        await db_engine.dispose()
        logger.info("Shutdown complete")


def _build_operation_id(route: APIRoute) -> str:
    # Produces ids like "health-check_liveness" so generated API clients
    # get readable method names instead of FastAPI's path-based default.
    tag = route.tags[0] if route.tags else "default"
    return f"{str(tag).lower()}-{route.name}"


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()
    configure_logging(settings.log_level, use_json=settings.log_json)

    docs_enabled = settings.enable_docs and not settings.is_production
    app = FastAPI(
        title=settings.service_name,
        version=settings.service_version,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
        generate_unique_id_function=_build_operation_id,
    )

    # Middleware added last runs first, so the request context wraps
    # everything, including CORS preflight responses.
    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(api_v1_router)
    return app


app = create_app()
