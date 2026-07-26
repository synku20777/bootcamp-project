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


def _detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return API_UNREADABLE_RESPONSE_MESSAGE
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if not isinstance(detail, str) and isinstance(payload, dict):
        error = payload.get("error")
        detail = error.get("message") if isinstance(error, dict) else None
    return detail if isinstance(detail, str) else "The API request failed."


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
        logger.warning(
            RESPONSE_FAILED_EVENT,
            extra={STATUS_CODE_FIELD: response.status_code},
        )
        raise DashboardApiError(_detail(response))

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
        logger.warning(
            RESPONSE_FAILED_EVENT,
            extra={STATUS_CODE_FIELD: response.status_code},
        )
        raise DashboardApiError(_detail(response))

    try:
        return response.json()
    except ValueError as exc:
        logger.warning(RESPONSE_INVALID_EVENT)
        raise DashboardApiError(API_UNREADABLE_RESPONSE_MESSAGE) from exc
