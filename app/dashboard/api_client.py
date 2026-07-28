from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

API_UNAVAILABLE_MESSAGE = "The API is currently unavailable."
API_UNREADABLE_RESPONSE_MESSAGE = "The API returned an unreadable response."
REQUEST_FAILED_EVENT = "dashboard_api_request_failed"
RESPONSE_FAILED_EVENT = "dashboard_api_response_failed"
RESPONSE_INVALID_EVENT = "dashboard_api_response_invalid"
REQUEST_ERROR_TYPE_FIELD = "request_error_type"
STATUS_CODE_FIELD = "status_code"


class DashboardApiError(Exception):
    """A safe API error suitable for display in the dashboard."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "api_unavailable",
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.request_id = request_id


def _response_error(response: requests.Response) -> DashboardApiError:
    try:
        payload = response.json()
    except ValueError:
        return DashboardApiError(
            API_UNREADABLE_RESPONSE_MESSAGE,
            code="api_response_invalid",
            request_id=response.headers.get("X-Request-ID"),
        )
    detail = payload.get("detail") if isinstance(payload, dict) else None
    code = "api_request_failed"
    request_id = response.headers.get("X-Request-ID")
    if not isinstance(detail, str) and isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            detail = error.get("message")
            code = str(error.get("code") or code)
            request_id = str(error.get("request_id") or request_id or "") or None
    return DashboardApiError(
        detail if isinstance(detail, str) else "The API request failed.",
        code=code,
        request_id=request_id,
    )


def get_json(
    base_url: str,
    path: str,
    *,
    params: list[tuple[str, str]] | dict[str, str] | None = None,
    timeout: float = 30,
) -> Any:
    try:
        response = requests.get(
            f"{base_url}{path}",
            params=params,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        logger.warning(
            REQUEST_FAILED_EVENT,
            extra={REQUEST_ERROR_TYPE_FIELD: type(exc).__name__},
        )
        raise DashboardApiError(API_UNAVAILABLE_MESSAGE) from exc

    if not response.ok:
        error = _response_error(response)
        logger.warning(
            RESPONSE_FAILED_EVENT,
            extra={
                STATUS_CODE_FIELD: response.status_code,
                "error_code": error.code,
                "upstream_request_id": error.request_id,
            },
        )
        raise error

    try:
        return response.json()
    except ValueError as exc:
        logger.warning(RESPONSE_INVALID_EVENT)
        raise DashboardApiError(API_UNREADABLE_RESPONSE_MESSAGE) from exc


def post_json(
    base_url: str,
    path: str,
    *,
    payload: dict[str, Any],
    timeout: float = 30,
) -> Any:
    try:
        response = requests.post(
            f"{base_url}{path}",
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        logger.warning(
            REQUEST_FAILED_EVENT,
            extra={REQUEST_ERROR_TYPE_FIELD: type(exc).__name__},
        )
        raise DashboardApiError(API_UNAVAILABLE_MESSAGE) from exc

    if not response.ok:
        error = _response_error(response)
        logger.warning(
            RESPONSE_FAILED_EVENT,
            extra={
                STATUS_CODE_FIELD: response.status_code,
                "error_code": error.code,
                "upstream_request_id": error.request_id,
            },
        )
        raise error

    try:
        return response.json()
    except ValueError as exc:
        logger.warning(RESPONSE_INVALID_EVENT)
        raise DashboardApiError(API_UNREADABLE_RESPONSE_MESSAGE) from exc
