from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


class DashboardApiError(Exception):
    """A safe API error suitable for display in the dashboard."""


def _detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return "The API returned an unreadable response."
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
            "dashboard_api_request_failed",
            extra={"request_error_type": type(exc).__name__},
        )
        raise DashboardApiError("The API is currently unavailable.") from exc

    if not response.ok:
        logger.warning(
            "dashboard_api_response_failed",
            extra={"status_code": response.status_code},
        )
        raise DashboardApiError(_detail(response))

    try:
        return response.json()
    except ValueError as exc:
        logger.warning("dashboard_api_response_invalid")
        raise DashboardApiError("The API returned an unreadable response.") from exc


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
            "dashboard_api_request_failed",
            extra={"request_error_type": type(exc).__name__},
        )
        raise DashboardApiError("The API is currently unavailable.") from exc

    if not response.ok:
        logger.warning(
            "dashboard_api_response_failed",
            extra={"status_code": response.status_code},
        )
        raise DashboardApiError(_detail(response))

    try:
        return response.json()
    except ValueError as exc:
        logger.warning("dashboard_api_response_invalid")
        raise DashboardApiError("The API returned an unreadable response.") from exc
