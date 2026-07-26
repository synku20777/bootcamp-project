from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.exceptions import (
    CountryNotFoundError,
    DataSourceUnavailableError,
    DomainValidationError,
)

logger = logging.getLogger(__name__)


def _payload(request: Request, code: str, message: str) -> dict[str, object]:
    return {
        "error": {"code": code, "message": message},
        "request_id": getattr(request.state, "request_id", None),
    }


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(CountryNotFoundError)
    async def country_not_found(
        request: Request,
        exc: CountryNotFoundError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=_payload(request, "country_not_found", str(exc)),
        )

    @app.exception_handler(DomainValidationError)
    async def domain_validation(
        request: Request,
        exc: DomainValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_payload(request, "domain_validation_error", str(exc)),
        )

    @app.exception_handler(DataSourceUnavailableError)
    async def data_source_unavailable(
        request: Request,
        exc: DataSourceUnavailableError,
    ) -> JSONResponse:
        logger.warning(
            "data_source_unavailable",
            extra={"source": exc.source},
        )
        return JSONResponse(
            status_code=503,
            content=_payload(request, "data_source_unavailable", str(exc)),
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unexpected_application_error",
            extra={"exception_type": type(exc).__name__},
        )
        return JSONResponse(
            status_code=500,
            content=_payload(
                request,
                "internal_error",
                "An unexpected error occurred.",
            ),
        )
