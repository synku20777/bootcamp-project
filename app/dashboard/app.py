from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import dash_mantine_components as dmc
from dash import Dash, Input, Output, State, callback, ctx, dcc, html
from dash.exceptions import PreventUpdate
from dash_iconify import DashIconify

from app.config import get_dashboard_settings
from app.dashboard.api_client import DashboardApiError, get_json, post_json
from app.dashboard.layouts import (
    annotation_page,
    comparison_page,
    country_page,
    forecast_page,
    not_found_page,
    overview_page,
    patterns_page,
    status_badge,
    status_page,
)
from app.dashboard.pages.annotations import (
    _load_annotations,
    _matches_annotation_filter,
    notify_annotation_saved,
    render_annotation_content,
    update_annotation_page,
)
from app.dashboard.pages.catalog import load_country_catalog
from app.dashboard.pages.common import (
    _context_change_detail,
    _context_change_value,
    _error,
    _format_integer,
    _format_rate,
    _normalized_date_range,
    _parse_dashboard_date,
    _success,
)
from app.dashboard.pages.comparison import (
    _world_bank_comparison_section,
    _world_bank_comparison_table,
    load_comparison_page,
    render_comparison_content,
    update_world_bank_comparison_chart,
)
from app.dashboard.pages.country import (
    _context_change_row,
    _world_bank_context_cards,
    _world_bank_gdp_panel,
    load_country_page,
    render_country_content,
)
from app.dashboard.pages.forecast import load_forecast_page, render_forecast_content
from app.dashboard.pages.overview import load_overview, render_overview_content
from app.dashboard.pages.patterns import (
    _patterns_table,
    load_patterns_page,
    render_patterns_content,
)
from app.dashboard.theme import DASHBOARD_THEME
from app.logging_config import configure_logging

__all__ = [
    "NAVIGATION_ITEMS",
    "_context_change_detail",
    "_context_change_row",
    "_context_change_value",
    "_error",
    "_format_integer",
    "_format_rate",
    "_load_annotations",
    "_matches_annotation_filter",
    "_navigation",
    "_normalized_date_range",
    "_parse_dashboard_date",
    "_patterns_table",
    "_success",
    "_world_bank_comparison_section",
    "_world_bank_comparison_table",
    "_world_bank_context_cards",
    "_world_bank_gdp_panel",
    "app",
    "check_api_liveness",
    "check_api_readiness",
    "ctx",
    "get_json",
    "load_comparison_page",
    "load_country_catalog",
    "load_country_page",
    "load_forecast_page",
    "load_overview",
    "load_patterns_page",
    "notify_annotation_saved",
    "post_json",
    "render_annotation_content",
    "render_comparison_content",
    "render_country_content",
    "render_forecast_content",
    "render_overview_content",
    "render_patterns_content",
    "render_snowflake_status",
    "retrieve_snowflake_status",
    "route_page",
    "server",
    "toggle_desktop_sidebar",
    "toggle_mobile_sidebar",
    "update_annotation_page",
    "update_app_shell_navbar",
    "update_world_bank_comparison_chart",
]

settings = get_dashboard_settings()
logger = logging.getLogger(__name__)
assets_directory = Path(__file__).with_name("assets")

external_stylesheets = [
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700"
    "&family=Poppins:wght@500;600;700&display=swap"
]

app = Dash(
    __name__,
    title="COVID-19 Analytics Platform",
    assets_folder=str(assets_directory),
    suppress_callback_exceptions=True,
    external_stylesheets=external_stylesheets,
)
server = app.server

NAVIGATION_ITEMS = [
    {"label": "Status", "path": "/", "icon": "tabler:activity-heartbeat"},
    {"label": "Overview", "path": "/overview", "icon": "tabler:world"},
    {"label": "Patterns", "path": "/patterns", "icon": "tabler:trending-up"},
    {"label": "Country Explorer", "path": "/country", "icon": "tabler:chart-line"},
    {"label": "Comparison", "path": "/compare", "icon": "tabler:arrows-diff"},
    {"label": "Forecast", "path": "/forecast", "icon": "tabler:timeline-event"},
    {"label": "Annotations", "path": "/annotations", "icon": "tabler:message-plus"},
]


def _navigation() -> dmc.Stack:
    return dmc.Stack(
        gap="xs",
        children=[
            dmc.NavLink(
                label=item["label"],
                href=item["path"],
                leftSection=DashIconify(icon=item["icon"], width=20),
                active="exact" if item["path"] == "/" else "partial",
                className="nav-link",
                **{"aria-label": item["label"]},
            )
            for item in NAVIGATION_ITEMS
        ],
    )


app.layout = dmc.MantineProvider(
    forceColorScheme="dark",
    theme=DASHBOARD_THEME,
    children=[
        dcc.Location(id="page-location"),
        dcc.Store(id="snowflake-status", storage_type="session"),
        dcc.Store(id="country-catalog", storage_type="session"),
        dcc.Store(id="overview-page-data", storage_type="memory"),
        dcc.Store(id="patterns-page-data", storage_type="memory"),
        dcc.Store(id="country-page-data", storage_type="memory"),
        dcc.Store(id="comparison-page-data", storage_type="memory"),
        dcc.Store(id="forecast-page-data", storage_type="memory"),
        dcc.Store(id="annotation-page-data", storage_type="memory"),
        dcc.Store(
            id="desktop-sidebar-state",
            storage_type="local",
            data={"compact": False},
        ),
        dcc.Store(
            id="mobile-sidebar-state",
            storage_type="memory",
            data={"open": False},
        ),
        dcc.Store(id="layout-resize-signal", storage_type="memory"),
        dmc.NotificationContainer(
            id="notification-container",
            position="top-right",
        ),
        dmc.AppShell(
            id="app-shell",
            header={"height": 64},
            padding="md",
            navbar={
                "width": {"base": 280, "sm": 260},
                "breakpoint": "sm",
                "collapsed": {"desktop": False, "mobile": True},
            },
            children=[
                dmc.AppShellHeader(
                    dmc.Group(
                        h="100%",
                        px="md",
                        align="center",
                        children=[
                            dmc.ActionIcon(
                                DashIconify(icon="tabler:menu-2", width=22),
                                id="mobile-sidebar-toggle",
                                hiddenFrom="sm",
                                n_clicks=0,
                                variant="subtle",
                                size="lg",
                                **{"aria-label": "Toggle navigation"},
                            ),
                            dmc.ActionIcon(
                                DashIconify(icon="tabler:layout-sidebar"),
                                id="desktop-sidebar-toggle",
                                visibleFrom="sm",
                                variant="subtle",
                                size="lg",
                                **{"aria-label": "Toggle sidebar"},
                            ),
                            dcc.Link(
                                dmc.Text(
                                    "COVID / DATA",
                                    fw=700,
                                    size="lg",
                                    ml="sm",
                                    c="teal",
                                ),
                                href="/",
                                style={"textDecoration": "none"},
                            ),
                        ],
                    )
                ),
                dmc.AppShellNavbar(
                    id="app-shell-navbar",
                    className="app-navbar",
                    p="md",
                    children=[_navigation()],
                ),
                dmc.AppShellMain(html.Main(id="page-content", className="page-shell")),
            ],
        ),
    ],
)


@callback(
    Output("desktop-sidebar-state", "data"),
    Input("desktop-sidebar-toggle", "n_clicks"),
    State("desktop-sidebar-state", "data"),
    prevent_initial_call=True,
)
def toggle_desktop_sidebar(
    n_clicks: int | None,
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    if not n_clicks:
        raise PreventUpdate
    compact = not (state and state.get("compact", False))
    return {"compact": compact}


@callback(
    Output("mobile-sidebar-state", "data"),
    Input("mobile-sidebar-toggle", "n_clicks"),
    Input("page-location", "pathname"),
    State("mobile-sidebar-state", "data"),
    prevent_initial_call=True,
)
def toggle_mobile_sidebar(
    n_clicks: int | None,
    _pathname: str | None,
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    trigger = ctx.triggered_id
    if trigger == "page-location":
        return {"open": False}
    open_state = not (state and state.get("open", False))
    return {"open": open_state}


@callback(
    Output("app-shell", "navbar"),
    Output("app-shell-navbar", "className"),
    Input("desktop-sidebar-state", "data"),
    Input("mobile-sidebar-state", "data"),
)
def update_app_shell_navbar(
    desktop_state: dict[str, Any] | None,
    mobile_state: dict[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    desktop_compact = bool(desktop_state and desktop_state.get("compact", False))
    mobile_open = bool(mobile_state and mobile_state.get("open", False))

    navbar = {
        "width": {"base": 280, "sm": 72 if desktop_compact else 260},
        "breakpoint": "sm",
        "collapsed": {
            "desktop": False,
            "mobile": not mobile_open,
        },
    }
    navbar_class = "app-navbar app-navbar--compact" if desktop_compact else "app-navbar"
    return navbar, navbar_class


app.clientside_callback(
    """
    function(desktopState, mobileState) {
        window.setTimeout(function() {
            window.dispatchEvent(new Event("resize"));
        }, 220);
        return Date.now();
    }
    """,
    Output("layout-resize-signal", "data"),
    Input("desktop-sidebar-state", "data"),
    Input("mobile-sidebar-state", "data"),
    prevent_initial_call=True,
)


@callback(
    Output("page-content", "children"),
    Input("page-location", "pathname"),
    Input("country-catalog", "data"),
)
def route_page(
    pathname: str | None,
    catalog_state: dict[str, Any] | None,
) -> html.Div:
    routes = {
        "/": lambda: status_page(settings.dashboard_public_api_base_url),
        "/overview": overview_page,
        "/patterns": lambda: patterns_page(catalog_state),
        "/country": lambda: country_page(catalog_state),
        "/compare": lambda: comparison_page(catalog_state),
        "/forecast": lambda: forecast_page(catalog_state),
        "/annotations": lambda: annotation_page(catalog_state),
    }
    page_factory = routes.get(pathname or "/")
    return page_factory() if page_factory else not_found_page()


@callback(
    Output("api-live-status", "children"),
    Input("page-location", "pathname"),
    running=[(Output("api-live-loading", "visible"), True, False)],
)
def check_api_liveness(pathname: str | None) -> html.Div | Any:
    if pathname != "/":
        raise PreventUpdate
    try:
        get_json(
            settings.dashboard_api_base_url,
            "/health/live",
            timeout=3,
        )
    except DashboardApiError:
        return status_badge("Unavailable", "error", "FastAPI did not respond")
    return status_badge("Running", "success", "Process liveness confirmed")


@callback(
    Output("api-ready-status", "children"),
    Input("page-location", "pathname"),
    running=[(Output("api-ready-loading", "visible"), True, False)],
)
def check_api_readiness(pathname: str | None) -> html.Div | Any:
    if pathname != "/":
        raise PreventUpdate
    try:
        get_json(
            settings.dashboard_api_base_url,
            "/health/ready",
            timeout=5,
        )
    except DashboardApiError as exc:
        detail = str(exc)
        if exc.request_id:
            detail += f" Request ID: {exc.request_id}"
        return status_badge("Dependency unavailable", "error", detail)
    return status_badge("Ready", "success", "Redis and MongoDB are available")


@callback(
    Output("snowflake-status", "data"),
    Input("check-snowflake", "n_clicks", allow_optional=True),
    prevent_initial_call=True,
    running=[
        (Output("check-snowflake", "disabled"), True, False),
        (Output("snowflake-loading", "visible"), True, False),
    ],
)
def retrieve_snowflake_status(_n_clicks: int) -> dict[str, Any]:
    if not _n_clicks:
        raise PreventUpdate
    try:
        payload = get_json(
            settings.dashboard_api_base_url,
            "/health/snowflake",
            timeout=30,
        )
        return {
            "state": "success",
            "checked_at": payload["checked_at"],
            "latency_ms": payload["latency_ms"],
        }
    except (DashboardApiError, KeyError, ValueError) as exc:
        logger.warning(
            "dashboard_snowflake_check_failed",
            extra={"request_error_type": type(exc).__name__},
        )
        result: dict[str, Any] = {
            "state": "error",
            "checked_at": datetime.now(UTC).isoformat(),
        }
        if isinstance(exc, DashboardApiError):
            result.update(
                {
                    "message": str(exc),
                    "code": exc.code,
                    "request_id": exc.request_id,
                }
            )
        return result


@callback(
    Output("snowflake-status-view", "children"),
    Input("snowflake-status", "data"),
)
def render_snowflake_status(data: dict[str, Any] | None) -> html.Div:
    if not data:
        return status_badge("Not checked", "neutral", "No warehouse query made")
    if data.get("state") == "success":
        detail = f"{data['latency_ms']} ms · {data['checked_at']}"
        return status_badge("Connected", "success", detail)
    code = data.get("code")
    setup_codes = {
        "snowflake_configuration_invalid",
        "snowflake_account_invalid",
        "snowflake_role_unauthorized",
        "snowflake_warehouse_unavailable",
        "snowflake_permission_denied",
        "analytics_objects_missing",
    }
    label = "Setup incomplete" if code in setup_codes else "Dependency unavailable"
    detail = data.get("message", "Snowflake could not be checked.")
    if data.get("request_id"):
        detail += f" Request ID: {data['request_id']}"
    if data.get("checked_at"):
        detail += f" Last checked: {data['checked_at']}"
    return status_badge(label, "error", detail)


if __name__ == "__main__":
    configure_logging(settings.service_name, settings.log_level)
    app.run(host="0.0.0.0", port=8050, debug=False)
