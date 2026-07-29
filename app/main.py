from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pymongo import MongoClient
from redis import Redis

from app.api.routes_annotations import router as annotations_router
from app.api.routes_covid import router as covid_router
from app.api.routes_health import router as health_router
from app.config import get_settings
from app.error_handlers import register_error_handlers
from app.exceptions import DataSourceUnavailableError
from app.logging_config import configure_logging
from app.middleware import register_request_middleware
from app.world_bank_manifest import committed_snapshot_id


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.settings = settings
    try:
        app.state.world_bank_snapshot_id = committed_snapshot_id(
            settings.world_bank_manifest_path
        )
    except DataSourceUnavailableError:
        # Optional context is disabled independently; COVID and forecast routes
        # remain available while deployment artifacts are corrected.
        app.state.world_bank_snapshot_id = None
    app.state.mongo_client = MongoClient(
        settings.mongodb_uri,
        connect=False,
        tz_aware=True,
        serverSelectionTimeoutMS=3000,
    )
    app.state.redis_client = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
    )

    try:
        yield
    finally:
        app.state.mongo_client.close()
        app.state.redis_client.close()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.service_name, settings.log_level)

    application = FastAPI(
        title="COVID-19 Analytics API",
        description="Cached analytics and forecasting API for the COVID-19 platform.",
        version="0.4.0",
        lifespan=lifespan,
    )
    register_request_middleware(application)
    register_error_handlers(application)
    application.include_router(health_router)
    application.include_router(covid_router)
    application.include_router(annotations_router)

    @application.get("/")
    def root() -> dict[str, str]:
        return {
            "service": "COVID-19 Analytics API",
            "status": "running",
            "docs": "/docs",
        }

    return application


app = create_app()
