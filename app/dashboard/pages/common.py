from __future__ import annotations

import sys
from datetime import date
from typing import Any

from dash import ctx as dash_ctx

from app.config import get_dashboard_settings
from app.dashboard.api_client import DashboardApiError
from app.dashboard.api_client import get_json as api_get_json
from app.dashboard.api_client import post_json as api_post_json
from app.dashboard.layouts import DEFAULT_END_DATE, DEFAULT_START_DATE

settings = get_dashboard_settings()


def _legacy_dependency(name: str, fallback: Any) -> Any:
    """Resolve dependencies through app.py so legacy patch targets still work."""
    app_module = sys.modules.get("app.dashboard.app")
    return getattr(app_module, name, fallback) if app_module else fallback


def get_json(*args: Any, **kwargs: Any) -> Any:
    return _legacy_dependency("get_json", api_get_json)(*args, **kwargs)


def post_json(*args: Any, **kwargs: Any) -> Any:
    return _legacy_dependency("post_json", api_post_json)(*args, **kwargs)


def triggered_id() -> str | None:
    return _legacy_dependency("ctx", dash_ctx).triggered_id


def _success(payload: Any) -> dict[str, Any]:
    return {"state": "success", "payload": payload}


def _error(exc: DashboardApiError) -> dict[str, str | None]:
    return {
        "state": "error",
        "message": str(exc),
        "code": exc.code,
        "request_id": exc.request_id,
    }


def _parse_dashboard_date(value: str | date | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _normalized_date_range(
    start_value: str | date | None,
    end_value: str | date | None,
    *,
    required: bool,
) -> tuple[str | None, str | None, str | None]:
    start_date = _parse_dashboard_date(start_value)
    end_date = _parse_dashboard_date(end_value)
    if required and (start_date is None or end_date is None):
        return None, None, "Choose a complete date range."
    if (start_value and start_date is None) or (end_value and end_date is None):
        return None, None, "Use valid calendar dates."
    if start_date and end_date and start_date > end_date:
        return None, None, "Start date must be on or before end date."
    selected_dates = (value for value in (start_date, end_date) if value is not None)
    if any(
        value < DEFAULT_START_DATE or value > DEFAULT_END_DATE
        for value in selected_dates
    ):
        return (
            None,
            None,
            "Dates must be within the available range "
            f"{DEFAULT_START_DATE.isoformat()} to {DEFAULT_END_DATE.isoformat()}.",
        )
    return (
        start_date.isoformat() if start_date else None,
        end_date.isoformat() if end_date else None,
        None,
    )


def _format_integer(value: int | float | None) -> str:
    return "—" if value is None else f"{value:,.0f}"


def _format_rate(value: int | float | None) -> str:
    return "—" if value is None else f"{value:,.2f}%"


def _context_change_value(change: dict[str, Any]) -> str:
    value = change.get("value")
    if change.get("status") != "available" or value is None:
        return "Not available"
    return f"{value:+,.2f}%"


def _context_change_detail(change: dict[str, Any]) -> str | None:
    if change.get("status") == "missing_input":
        return "A required annual GDP value is missing."
    if change.get("status") == "zero_denominator":
        return "The baseline GDP value is zero."
    return None
