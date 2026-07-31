from __future__ import annotations

from typing import Any

from dash import Input, Output, State, callback, no_update
from dash.exceptions import PreventUpdate

from app.dashboard.pages.common import (
    DashboardApiError,
    _error,
    _success,
    get_json,
    settings,
    triggered_id,
)


@callback(
    Output("country-catalog", "data"),
    Input("page-location", "pathname"),
    Input("retry-catalog", "n_clicks", allow_optional=True),
    State("country-catalog", "data"),
    prevent_initial_call=True,
)
def load_country_catalog(
    pathname: str | None,
    _retry_clicks: int | None,
    current: dict[str, Any] | None,
) -> dict[str, Any] | Any:
    if pathname not in {
        "/patterns",
        "/country",
        "/compare",
        "/forecast",
        "/annotations",
    }:
        raise PreventUpdate
    if (
        current
        and current.get("state") == "success"
        and triggered_id() != "retry-catalog"
    ):
        return no_update
    try:
        return _success(get_json(settings.dashboard_api_base_url, "/countries"))
    except DashboardApiError as exc:
        return _error(exc)
