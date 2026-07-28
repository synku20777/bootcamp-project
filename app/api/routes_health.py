from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter

from fastapi import APIRouter
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError

from app.dependencies import (
    MongoDependency,
    RedisDependency,
    SnowflakeRepositoryDependency,
)
from app.exceptions import cache_unavailable_error, mongodb_unavailable_error
from app.models.health import LiveStatus, ReadyStatus, SnowflakeStatus

router = APIRouter(tags=["health"])


@router.get("/health", response_model=LiveStatus)
@router.get("/health/live", response_model=LiveStatus)
def live() -> LiveStatus:
    """Report process liveness without touching dependency clients."""

    return LiveStatus()


@router.get("/health/ready", response_model=ReadyStatus)
def ready(
    mongo_client: MongoDependency,
    redis_client: RedisDependency,
) -> ReadyStatus:
    """Check the operational dependencies used by application requests."""

    try:
        mongo_client.admin.command("ping")
    except PyMongoError as exc:
        raise mongodb_unavailable_error() from exc

    try:
        redis_client.ping()
    except RedisError as exc:
        raise cache_unavailable_error() from exc

    return ReadyStatus()


@router.get("/health/snowflake", response_model=SnowflakeStatus)
def snowflake(
    repository: SnowflakeRepositoryDependency,
) -> SnowflakeStatus:
    """Perform an explicit, uncached check of Snowflake and required marts."""

    started = perf_counter()
    repository.check_health()
    latency_ms = round((perf_counter() - started) * 1000)
    return SnowflakeStatus(
        checked_at=datetime.now(UTC),
        latency_ms=latency_ms,
        objects={
            "COVID_ENRICHED": "accessible",
            "COUNTRY_LATEST_METRICS": "accessible",
        },
    )
