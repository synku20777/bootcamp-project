from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

import dash_mantine_components as dmc
from dash import Dash, Input, Output, State, callback, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate
from dash_iconify import DashIconify

from app.config import get_dashboard_settings
from app.dashboard.api_client import DashboardApiError, get_json, post_json
from app.dashboard.charts import (
    COLORS,
    comparison_figure,
    metric_figure,
    overview_bar,
    overview_map,
)
from app.dashboard.components import create_alert, create_chart_card, create_kpi_card
from app.dashboard.layouts import (
    DEFAULT_END_DATE,
    DEFAULT_START_DATE,
    annotation_page,
    comparison_page,
    country_page,
    not_found_page,
    overview_page,
    status_badge,
    status_page,
)
from app.dashboard.theme import DASHBOARD_THEME
from app.logging_config import configure_logging

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
    {"label": "Country Explorer", "path": "/country", "icon": "tabler:chart-line"},
    {"label": "Comparison", "path": "/compare", "icon": "tabler:arrows-diff"},
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
        dcc.Store(id="country-page-data", storage_type="memory"),
        dcc.Store(id="comparison-page-data", storage_type="memory"),
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
        "/country": lambda: country_page(catalog_state),
        "/compare": lambda: comparison_page(catalog_state),
        "/annotations": lambda: annotation_page(catalog_state),
    }
    page_factory = routes.get(pathname or "/")
    return page_factory() if page_factory else not_found_page()


def _success(payload: Any) -> dict[str, Any]:
    return {"state": "success", "payload": payload}


def _error(exc: DashboardApiError) -> dict[str, str]:
    return {"state": "error", "message": str(exc)}


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
    if pathname not in {"/country", "/compare", "/annotations"}:
        raise PreventUpdate
    if (
        current
        and current.get("state") == "success"
        and ctx.triggered_id != "retry-catalog"
    ):
        return no_update
    try:
        return _success(get_json(settings.dashboard_api_base_url, "/countries"))
    except DashboardApiError as exc:
        return _error(exc)


@callback(
    Output("overview-page-data", "data"),
    Input("page-location", "pathname"),
    Input("overview-retry", "n_clicks", allow_optional=True),
    State("overview-page-data", "data"),
    prevent_initial_call=True,
    running=[(Output("overview-loading", "visible"), True, False)],
)
def load_overview(
    pathname: str | None,
    _retry_clicks: int | None,
    current: dict[str, Any] | None,
) -> dict[str, Any] | Any:
    if pathname != "/overview":
        raise PreventUpdate
    if (
        current
        and current.get("state") == "success"
        and ctx.triggered_id != "overview-retry"
    ):
        return no_update
    try:
        return _success(
            get_json(settings.dashboard_api_base_url, "/dashboard/overview")
        )
    except DashboardApiError as exc:
        return _error(exc)


def _format_integer(value: int | float | None) -> str:
    return "—" if value is None else f"{value:,.0f}"


def _format_rate(value: int | float | None) -> str:
    return "—" if value is None else f"{value:,.2f}%"


@callback(Output("overview-content", "children"), Input("overview-page-data", "data"))
def render_overview_content(state: dict[str, Any] | None) -> html.Div:
    if not state:
        return status_badge("Loading", "neutral", "Requesting overview data")
    if state.get("state") == "error":
        return create_alert(state["message"])

    payload = state["payload"]
    totals = payload["totals"]
    locations = payload["locations"]
    return dmc.Box(
        [
            dmc.Grid(
                children=[
                    dmc.GridCol(
                        span={"base": 12, "sm": 6, "md": 3},
                        children=create_kpi_card(
                            "Countries",
                            _format_integer(totals["countries"]),
                            "tabler:map",
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "sm": 6, "md": 3},
                        children=create_kpi_card(
                            "Total cases",
                            _format_integer(totals["total_cases"]),
                            "tabler:users",
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "sm": 6, "md": 3},
                        children=create_kpi_card(
                            "Total deaths",
                            _format_integer(totals["total_deaths"]),
                            "tabler:skull",
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "sm": 6, "md": 3},
                        children=create_kpi_card(
                            "Mortality rate",
                            _format_rate(totals["mortality_rate_percent"]),
                            "tabler:percentage",
                        ),
                    ),
                ],
                mb="md",
            ),
            dmc.Text(
                f"Latest reporting date: {totals['report_date']}",
                size="sm",
                c="dimmed",
                mb="md",
            ),
            dmc.Grid(
                children=[
                    dmc.GridCol(
                        span={"base": 12, "lg": 6},
                        children=create_chart_card(
                            overview_bar(
                                locations,
                                "cases_cumulative",
                                "Top 10 countries by cases",
                                COLORS[0],
                            )
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "lg": 6},
                        children=create_chart_card(
                            overview_bar(
                                locations,
                                "deaths_cumulative",
                                "Top 10 countries by deaths",
                                COLORS[2],
                            )
                        ),
                    ),
                    dmc.GridCol(
                        span=12, children=create_chart_card(overview_map(locations))
                    ),
                ]
            ),
        ]
    )


@callback(
    Output("country-page-data", "data"),
    Input("country-select", "value", allow_optional=True),
    Input("country-metric", "value", allow_optional=True),
    Input("country-start-date", "value", allow_optional=True),
    Input("country-end-date", "value", allow_optional=True),
    Input("country-retry", "n_clicks", allow_optional=True),
    prevent_initial_call=True,
    running=[(Output("country-loading", "visible"), True, False)],
)
def load_country_page(
    identifier: str | None,
    metric: str | None,
    start_date: str | date | None,
    end_date: str | date | None,
    _retry_clicks: int | None,
) -> dict[str, Any]:
    if not identifier or not metric:
        return {
            "state": "error",
            "message": "Choose a country and metric.",
        }
    normalized_start, normalized_end, validation_error = _normalized_date_range(
        start_date,
        end_date,
        required=True,
    )
    if validation_error:
        return {"state": "error", "message": validation_error}
    try:
        payload = get_json(
            settings.dashboard_api_base_url,
            f"/dashboard/countries/{identifier}",
            params={
                "metric": metric,
                "start_date": normalized_start,
                "end_date": normalized_end,
            },
        )
        return _success(payload)
    except DashboardApiError as exc:
        return _error(exc)


@callback(Output("country-content", "children"), Input("country-page-data", "data"))
def render_country_content(state: dict[str, Any] | None) -> html.Div:
    if not state:
        return status_badge("Waiting", "neutral", "Choose country filters")
    if state.get("state") == "error":
        return create_alert(state["message"])

    payload = state["payload"]
    summary = payload["summary"]
    metric_title = payload["selected"]["metric"].replace("_", " ").title()
    return dmc.Box(
        [
            dmc.Group(
                align="center",
                mb="lg",
                children=[
                    dmc.Title(payload["country"], order=2),
                    dmc.Badge(
                        " · ".join(
                            value
                            for value in (payload.get("iso2"), payload.get("iso3"))
                            if value
                        ),
                        variant="dot",
                        color="blue",
                        size="lg",
                    ),
                ],
            ),
            dmc.Grid(
                children=[
                    dmc.GridCol(
                        span={"base": 12, "sm": 6, "md": 4, "lg": 2},
                        children=create_kpi_card(
                            "Population",
                            _format_integer(summary["population"]),
                            "tabler:users",
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "sm": 6, "md": 4, "lg": 2},
                        children=create_kpi_card(
                            "Cases",
                            _format_integer(summary["cases_cumulative"]),
                            "tabler:virus",
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "sm": 6, "md": 4, "lg": 2},
                        children=create_kpi_card(
                            "Deaths",
                            _format_integer(summary["deaths_cumulative"]),
                            "tabler:skull",
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "sm": 6, "md": 4, "lg": 3},
                        children=create_kpi_card(
                            "Cases / 100k",
                            _format_integer(summary["cases_per_100k"]),
                            "tabler:activity",
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "sm": 6, "md": 4, "lg": 3},
                        children=create_kpi_card(
                            "Mortality",
                            _format_rate(summary["mortality_rate_percent"]),
                            "tabler:percentage",
                        ),
                    ),
                ],
                mb="md",
            ),
            dmc.Grid(
                children=[
                    dmc.GridCol(
                        span={"base": 12, "lg": 6},
                        children=create_chart_card(
                            metric_figure(
                                payload["selected"]["points"],
                                metric_title,
                                color=COLORS[0],
                            )
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "lg": 6},
                        children=create_chart_card(
                            metric_figure(
                                payload["daily_cases"]["points"],
                                "Daily cases",
                                color=COLORS[1],
                                chart_type="bar",
                            )
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "lg": 6},
                        children=create_chart_card(
                            metric_figure(
                                payload["daily_deaths"]["points"],
                                "Daily deaths",
                                color=COLORS[2],
                                chart_type="bar",
                            )
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "lg": 6},
                        children=create_chart_card(
                            metric_figure(
                                payload["mortality"]["points"],
                                "Mortality rate (%)",
                                color=COLORS[3],
                            )
                        ),
                    ),
                ]
            ),
        ]
    )


@callback(
    Output("comparison-page-data", "data"),
    Input("comparison-countries", "value", allow_optional=True),
    Input("compare-start-date", "value", allow_optional=True),
    Input("compare-end-date", "value", allow_optional=True),
    Input("comparison-retry", "n_clicks", allow_optional=True),
    prevent_initial_call=True,
    running=[(Output("comparison-loading", "visible"), True, False)],
)
def load_comparison_page(
    countries: list[str] | None,
    start_date: str | date | None,
    end_date: str | date | None,
    _retry_clicks: int | None,
) -> dict[str, Any]:
    if not countries or not 2 <= len(countries) <= 10:
        return {"state": "error", "message": "Choose between 2 and 10 countries."}
    normalized_start, normalized_end, validation_error = _normalized_date_range(
        start_date,
        end_date,
        required=True,
    )
    if validation_error:
        return {"state": "error", "message": validation_error}

    params = [("country", country) for country in countries]
    params.extend((("start_date", normalized_start), ("end_date", normalized_end)))
    try:
        return _success(
            get_json(
                settings.dashboard_api_base_url,
                "/dashboard/compare",
                params=params,
            )
        )
    except DashboardApiError as exc:
        return _error(exc)


@callback(
    Output("comparison-content", "children"),
    Input("comparison-page-data", "data"),
)
def render_comparison_content(state: dict[str, Any] | None) -> html.Div:
    if not state:
        return status_badge("Waiting", "neutral", "Choose comparison filters")
    if state.get("state") == "error":
        return create_alert(state["message"])

    payload = state["payload"]
    missing = payload["countries_without_data"]
    return dmc.Box(
        [
            (
                create_alert("No observations for: " + ", ".join(missing), "warning")
                if missing
                else None
            ),
            dmc.Grid(
                children=[
                    dmc.GridCol(
                        span={"base": 12, "lg": 6},
                        children=create_chart_card(
                            comparison_figure(
                                payload["series"], "cases_per_100k", "Cases per 100,000"
                            )
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "lg": 6},
                        children=create_chart_card(
                            comparison_figure(
                                payload["series"],
                                "deaths_per_100k",
                                "Deaths per 100,000",
                            )
                        ),
                    ),
                    dmc.GridCol(
                        span=12,
                        children=create_chart_card(
                            comparison_figure(
                                payload["series"], "mortality", "Mortality rate (%)"
                            )
                        ),
                    ),
                ],
                mt="md" if missing else 0,
            ),
        ]
    )


def _load_annotations(
    country: str,
    metric: str | None,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, Any]:
    params: list[tuple[str, str]] = [("country", country)]
    if metric:
        params.append(("metric", metric))
    if start_date:
        params.append(("start_date", start_date))
    if end_date:
        params.append(("end_date", end_date))
    return _success(
        get_json(
            settings.dashboard_api_base_url,
            "/annotations",
            params=params,
        )
    )


def _matches_annotation_filter(
    annotation: dict[str, Any],
    metric: str | None,
    start_date: str | None,
    end_date: str | None,
) -> bool:
    if metric and annotation["metric"] != metric:
        return False
    report_date = annotation["report_date"]
    return not (
        (start_date and report_date < start_date)
        or (end_date and report_date > end_date)
    )


@callback(
    Output("annotation-page-data", "data"),
    Input("annotation-country", "value", allow_optional=True),
    Input("annotation-filter-metric", "value", allow_optional=True),
    Input("annotation-filter-start-date", "value", allow_optional=True),
    Input("annotation-filter-end-date", "value", allow_optional=True),
    Input("annotation-retry", "n_clicks", allow_optional=True),
    Input("annotation-submit", "n_clicks", allow_optional=True),
    State("annotation-report-date", "value", allow_optional=True),
    State("annotation-metric", "value", allow_optional=True),
    State("annotation-created-by", "value", allow_optional=True),
    State("annotation-comment", "value", allow_optional=True),
    State("annotation-page-data", "data"),
    prevent_initial_call=True,
    running=[(Output("annotation-loading", "visible"), True, False)],
)
def update_annotation_page(
    country: str | None,
    filter_metric: str | None,
    filter_start_date: str | date | None,
    filter_end_date: str | date | None,
    _retry_clicks: int | None,
    _submit_clicks: int | None,
    report_date: str | date | None,
    metric: str | None,
    created_by: str | None,
    comment: str | None,
    current: dict[str, Any] | None,
) -> dict[str, Any]:
    if not country:
        return {"state": "error", "message": "Choose a country."}

    normalized_filter_start, normalized_filter_end, filter_error = (
        _normalized_date_range(
            filter_start_date,
            filter_end_date,
            required=False,
        )
    )
    if filter_error:
        return {"state": "error", "message": filter_error}

    try:
        if ctx.triggered_id != "annotation-submit":
            return _load_annotations(
                country,
                filter_metric,
                normalized_filter_start,
                normalized_filter_end,
            )

        normalized_report_date, _, report_date_error = _normalized_date_range(
            report_date,
            report_date,
            required=True,
        )
        if report_date_error:
            return {"state": "error", "message": report_date_error}
        if not all(
            (
                metric,
                created_by and created_by.strip(),
                comment and comment.strip(),
            )
        ):
            return {
                "state": "error",
                "message": "Complete the report date, metric, name, and comment.",
            }
        created = post_json(
            settings.dashboard_api_base_url,
            "/annotations",
            payload={
                "country": country,
                "report_date": normalized_report_date,
                "metric": metric,
                "created_by": created_by.strip(),
                "comment": comment.strip(),
            },
        )
        existing = (
            list(current["payload"])
            if current and current.get("state") == "success"
            else []
        )
        if _matches_annotation_filter(
            created,
            filter_metric,
            normalized_filter_start,
            normalized_filter_end,
        ):
            existing.append(created)
            existing.sort(key=lambda item: (item["report_date"], item["created_at"]))
        return {
            "state": "success",
            "payload": existing,
            "notice": "Annotation saved.",
            "notification_id": f"annotation-{created['id']}",
        }
    except DashboardApiError as exc:
        return _error(exc)


@callback(
    Output("annotation-content", "children"),
    Input("annotation-page-data", "data"),
)
def render_annotation_content(state: dict[str, Any] | None) -> html.Div:
    if not state:
        return status_badge("Waiting", "neutral", "Choose annotation filters")
    if state.get("state") == "error":
        return create_alert(state["message"])

    annotations = state["payload"]
    records = [
        dmc.Card(
            withBorder=True,
            radius="md",
            mb="md",
            children=[
                dmc.Group(
                    justify="space-between",
                    mb="xs",
                    children=[
                        dmc.Text(annotation["country"], fw=700),
                        dmc.Badge(
                            annotation["metric"].replace("_", " ").title(),
                            variant="light",
                            color="blue",
                        ),
                    ],
                ),
                dmc.Text(
                    annotation["comment"],
                    size="sm",
                    mb="md",
                    style={"whiteSpace": "pre-wrap"},
                ),
                dmc.Text(
                    f"{annotation['report_date']} · {annotation['created_by']}",
                    size="xs",
                    c="dimmed",
                ),
            ],
        )
        for annotation in annotations
    ]
    return dmc.Box(
        [
            dmc.Title("Saved annotations", order=2, mb="lg"),
            (
                dmc.Stack(records)
                if records
                else create_alert("No annotations match these filters.", "info")
            ),
        ]
    )


@callback(
    Output("annotation-comment", "value"),
    Output("notification-container", "sendNotifications"),
    Input("annotation-page-data", "data"),
    prevent_initial_call=True,
)
def notify_annotation_saved(
    state: dict[str, Any] | None,
) -> tuple[str, list[dict[str, Any]]]:
    if not state or not state.get("notice"):
        raise PreventUpdate
    notification = {
        "action": "show",
        "id": state.get("notification_id", "annotation-saved"),
        "title": "Annotation saved",
        "message": state["notice"],
        "color": "teal",
        "autoClose": 4000,
        "withBorder": True,
    }
    return "", [notification]


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
        return {"state": "error"}


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
    return status_badge("Unavailable", "error", "Check API logs for details")


if __name__ == "__main__":
    configure_logging(settings.service_name, settings.log_level)
    app.run(host="0.0.0.0", port=8050, debug=False)
