from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from pymongo import MongoClient
from redis import Redis


app = FastAPI(
    title="COVID-19 Analytics API",
    description="API for the COVID-19 data engineering project.",
    version="0.1.0",
)


mongo_client = MongoClient(
    os.environ["MONGODB_URI"],
    serverSelectionTimeoutMS=3000,
)

redis_client = Redis.from_url(
    os.environ["REDIS_URL"],
    decode_responses=True,
    socket_connect_timeout=3,
)


@app.get("/")
def root() -> dict[str, str]:
    """Return basic API information."""
    return {
        "service": "COVID-19 Analytics API",
        "status": "running",
    }


@app.get("/health")
def health() -> dict[str, str]:
    """Check MongoDB and Redis connectivity."""
    service_status: dict[str, str] = {}

    try:
        mongo_client.admin.command("ping")
        service_status["mongodb"] = "ok"
    except Exception as exc:
        service_status["mongodb"] = f"error: {type(exc).__name__}"

    try:
        redis_client.ping()
        service_status["redis"] = "ok"
    except Exception as exc:
        service_status["redis"] = f"error: {type(exc).__name__}"

    if any(value != "ok" for value in service_status.values()):
        raise HTTPException(
            status_code=503,
            detail=service_status,
        )

    return {
        "status": "ok",
        **service_status,
    }