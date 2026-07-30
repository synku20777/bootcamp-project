from __future__ import annotations

import logging
from datetime import UTC, date, datetime
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
    case_increase_patterns_figure,
    comparison_figure,
    forecast_figure,
    metric_figure,
    overview_bar,
    overview_map,
    world_bank_comparison_figure,
    world_bank_gdp_figure,
)
from app.dashboard.components import (
    create_alert,
    create_chart_card,
    create_context_metric_card,
    create_empty_state,
    create_kpi_card,
)
from app.dashboard.layouts import (
    DEFAULT_END_DATE,
    DEFAULT_START_DATE,
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
from app.dashboard.theme import DASHBOARD_THEME
from app.dashboard.world_bank import (
    DEFAULT_WORLD_BANK_COMPARISON_METRIC,
    WORLD_BANK_BASELINE_METRICS,
    format_world_bank_indicator,
    indicator_is_available,
    world_bank_metric,
)
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


@callback(
    Output("patterns-page-data", "data"),
    Input("patterns-country", "value", allow_optional=True),
    Input("patterns-start-date", "value", allow_optional=True),
    Input("patterns-end-date", "value", allow_optional=True),
    Input("patterns-minimum-increases", "value", allow_optional=True),
    Input("patterns-retry", "n_clicks", allow_optional=True),
    prevent_initial_call=True,
    running=[(Output("patterns-loading", "visible"), True, False)],
)
def load_patterns_page(
    country: str | None,
    start_date: str | date | None,
    end_date: str | date | None,
    minimum_increases: str | int | None,
    _retry_clicks: int | None,
) -> dict[str, Any]:
    normalized_start, normalized_end, validation_error = _normalized_date_range(
        start_date,
        end_date,
        required=True,
    )
    if validation_error:
        return {"state": "error", "message": validation_error}
    try:
        minimum = int(minimum_increases or 3)
    except (TypeError, ValueError):
        return {"state": "error", "message": "Use a valid minimum increase."}
    if not 3 <= minimum <= 30:
        return {
            "state": "error",
            "message": "Minimum increases must be between 3 and 30.",
        }

    params = {
        "start_date": normalized_start,
        "end_date": normalized_end,
        "minimum_consecutive_increases": str(minimum),
        "limit": "100",
    }
    if country:
        params["country"] = country
    try:
        return _success(
            get_json(
                settings.dashboard_api_base_url,
                "/patterns/case-increases",
                params=params,
            )
        )
    except DashboardApiError as exc:
        return _error(exc)


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


def _world_bank_context_cards(context: dict[str, Any]) -> dmc.Box:
    cards = []
    for definition in WORLD_BANK_BASELINE_METRICS:
        indicator = context[definition.key]
        cards.append(
            create_context_metric_card(
                definition.title,
                format_world_bank_indicator(indicator, definition),
                definition.metadata,
                indicator["status"],
                definition.icon,
                note=definition.note,
            )
        )
    return dmc.Box(
        mb="md",
        children=[
            dmc.Group(
                justify="space-between",
                align="center",
                mb="xs",
                children=[
                    dmc.Title("World Bank country context", order=3),
                    dmc.Badge("Baseline context", variant="light", color="blue"),
                ],
            ),
            dmc.Text(
                "Baseline indicators provide descriptive country context; they do "
                "not establish causes of COVID outcomes.",
                size="sm",
                c="dimmed",
                mb="md",
            ),
            dmc.SimpleGrid(
                cols={"base": 1, "sm": 2, "lg": 5},
                spacing="md",
                children=cards,
            ),
        ],
    )


def _context_change_row(label: str, change: dict[str, Any]) -> dmc.Box:
    detail = _context_change_detail(change)
    return dmc.Box(
        children=[
            dmc.Group(
                justify="space-between",
                align="center",
                wrap="nowrap",
                children=[
                    dmc.Text(label, size="sm"),
                    dmc.Badge(
                        _context_change_value(change),
                        color=(
                            "blue" if change.get("status") == "available" else "gray"
                        ),
                        variant="light",
                    ),
                ],
            ),
            dmc.Text(detail, size="xs", c="dimmed", mt=4) if detail else None,
        ]
    )


def _world_bank_gdp_panel(context: dict[str, Any]) -> dmc.Paper:
    # GDP is the only WDI metric charted here because the public country contract
    # exposes annual history only for GDP; presenting other metrics as trends would
    # require a new API shape and would misstate the current baseline-only scope.
    return dmc.Paper(
        p="md",
        radius="md",
        withBorder=True,
        mt="md",
        children=[
            dmc.Title("GDP during the pandemic period", order=3, mb=4),
            dmc.Text(
                "Annual real GDP per capita is descriptive context, not a causal "
                "estimate of COVID impact.",
                size="sm",
                c="dimmed",
                mb="md",
            ),
            dmc.Grid(
                children=[
                    dmc.GridCol(
                        span={"base": 12, "lg": 8},
                        children=create_chart_card(
                            world_bank_gdp_figure(context["real_gdp_per_capita_annual"])
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "lg": 4},
                        children=dmc.Stack(
                            gap="md",
                            children=[
                                dmc.Text("Descriptive changes", fw=600),
                                _context_change_row(
                                    "2020 vs 2019",
                                    context["real_gdp_per_capita_change_2020_vs_2019"],
                                ),
                                dmc.Divider(),
                                _context_change_row(
                                    "2021 vs 2019",
                                    context["real_gdp_per_capita_change_2021_vs_2019"],
                                ),
                                dmc.Divider(),
                                _context_change_row(
                                    "2021 vs 2020",
                                    context["real_gdp_per_capita_change_2021_vs_2020"],
                                ),
                            ],
                        ),
                    ),
                ]
            ),
            dmc.Box(
                mt="md",
                children=create_alert(context["methodology"]["caveat"], "info"),
            ),
            dmc.Divider(mt="md", mb="sm"),
            dmc.Text(
                "World Development Indicators · Snapshot: " f"{context['snapshot_id']}",
                size="xs",
                c="dimmed",
            ),
        ],
    )


@callback(Output("overview-content", "children"), Input("overview-page-data", "data"))
def render_overview_content(state: dict[str, Any] | None) -> html.Div:
    if not state:
        return status_badge("Loading", "neutral", "Requesting overview data")
    if state.get("state") == "error":
        return create_alert(state["message"])

    payload = state["payload"]
    totals = payload["totals"]
    locations = payload["locations"]
    candidate_count = sum(
        location.get("denominator_publication_status") == "CANDIDATE"
        for location in locations
    )
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
            (
                create_alert(
                    f"{candidate_count} locations use candidate 2020 population "
                    "denominators for per-capita metrics.",
                    "warning",
                )
                if candidate_count
                else None
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


def _patterns_table(patterns: list[dict[str, Any]]) -> dmc.ScrollArea:
    headers = (
        "Country",
        "Start",
        "End",
        "Days",
        "Consecutive increases",
        "Start cases",
        "End cases",
    )
    rows = [
        dmc.TableTr(
            [
                dmc.TableTd(pattern["country"]),
                dmc.TableTd(pattern["start_date"]),
                dmc.TableTd(pattern["end_date"]),
                dmc.TableTd(_format_integer(pattern["days_in_pattern"])),
                dmc.TableTd(_format_integer(pattern["consecutive_increases"])),
                dmc.TableTd(_format_integer(pattern["start_cases"])),
                dmc.TableTd(_format_integer(pattern["end_cases"])),
            ]
        )
        for pattern in patterns
    ]
    return dmc.ScrollArea(
        scrollbars="x",
        type="always",
        offsetScrollbars="x",
        w="100%",
        children=dmc.Table(
            miw=960,
            withTableBorder=True,
            withColumnBorders=True,
            striped=True,
            highlightOnHover=True,
            tabularNums=True,
            verticalSpacing="sm",
            children=[
                dmc.TableThead(
                    dmc.TableTr(
                        [dmc.TableTh(dmc.Text(header, fw=600)) for header in headers]
                    )
                ),
                dmc.TableTbody(rows),
            ],
        ),
    )


@callback(Output("patterns-content", "children"), Input("patterns-page-data", "data"))
def render_patterns_content(state: dict[str, Any] | None) -> html.Div:
    if not state:
        return status_badge("Loading", "neutral", "Requesting pattern data")
    if state.get("state") == "error":
        return create_alert(state["message"])

    payload = state["payload"]
    patterns = payload["patterns"]
    if not patterns:
        return create_empty_state(
            "No sustained case-increase patterns match these filters.",
            "tabler:chart-bar-off",
        )

    summary = payload["summary"]
    longest = summary.get("longest_consecutive_increases")
    latest = summary.get("latest_pattern_end_date") or "—"
    return dmc.Box(
        [
            dmc.Grid(
                mb="md",
                children=[
                    dmc.GridCol(
                        span={"base": 12, "sm": 6, "md": 3},
                        children=create_kpi_card(
                            "Matching patterns",
                            _format_integer(summary["total_patterns"]),
                            "tabler:chart-dots-3",
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "sm": 6, "md": 3},
                        children=create_kpi_card(
                            "Countries",
                            _format_integer(summary["countries_with_patterns"]),
                            "tabler:map-pin",
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "sm": 6, "md": 3},
                        children=create_kpi_card(
                            "Longest run",
                            "—" if longest is None else f"{longest} increases",
                            "tabler:trending-up",
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "sm": 6, "md": 3},
                        children=create_kpi_card(
                            "Most recent pattern",
                            str(latest),
                            "tabler:calendar-event",
                        ),
                    ),
                ],
            ),
            dmc.Text(
                f"Showing {payload['returned_patterns']:,} of "
                f"{summary['total_patterns']:,} matching patterns, ordered by "
                "longest run.",
                size="sm",
                c="dimmed",
                mb="md",
            ),
            create_chart_card(case_increase_patterns_figure(patterns)),
            dmc.Paper(
                p="md",
                radius="md",
                withBorder=True,
                mt="md",
                children=[
                    dmc.Title("Pattern details", order=3, mb=4),
                    dmc.Text(
                        "Exact observations returned by the filtered API query.",
                        size="sm",
                        c="dimmed",
                        mb="md",
                    ),
                    _patterns_table(patterns),
                ],
            ),
            dmc.Text(
                "Snowflake MATCH_RECOGNIZE · Country-date grain · Source boundaries "
                "are never crossed",
                size="xs",
                c="dimmed",
                mt="md",
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
                            "COVID rate denominator, 2020",
                            _format_integer(summary["covid_rate_population_2020"]),
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
            (
                create_alert(
                    "Per-capita metrics for this country use a candidate 2020 "
                    "population denominator.",
                    "warning",
                )
                if summary.get("denominator_publication_status") == "CANDIDATE"
                else None
            ),
            # Reusing the combined page payload preserves the one-request contract.
            # The generic warning is intentional because context_status cannot
            # distinguish an unsupported identity from an inactive snapshot row.
            (
                _world_bank_context_cards(payload["context"])
                if payload.get("context")
                else create_alert(
                    "World Bank context is unavailable for this country or active "
                    "snapshot.",
                    "warning",
                )
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
            (
                _world_bank_gdp_panel(payload["context"])
                if payload.get("context")
                else None
            ),
        ]
    )


def _world_bank_comparison_table(series: list[dict[str, Any]]) -> dmc.ScrollArea:
    header_cells = [
        dmc.TableTh(
            dmc.Text("Country", size="sm", fw=600),
            miw=160,
        )
    ]
    header_cells.extend(
        dmc.TableTh(
            dmc.Stack(
                gap=2,
                miw=190,
                children=[
                    dmc.Text(definition.title, size="sm", fw=600),
                    dmc.Text(definition.metadata, size="xs", c="dimmed"),
                ],
            )
        )
        for definition in WORLD_BANK_BASELINE_METRICS
    )

    rows = []
    for index, country in enumerate(series):
        context = country.get("world_bank_context")
        value_cells = []
        for definition in WORLD_BANK_BASELINE_METRICS:
            indicator = context.get(definition.key) if context else None
            available = indicator_is_available(indicator)
            text_options = {
                "children": format_world_bank_indicator(indicator, definition),
                "size": "sm",
                "fw": 600 if available else 500,
            }
            # Mantine treats an explicit null color as a responsive-style object
            # in the browser. Omitting the property preserves the theme color and
            # avoids a console error for every available matrix cell.
            if not available:
                text_options["c"] = "dimmed"
            value_cells.append(dmc.TableTd(dmc.Text(**text_options)))
        rows.append(
            dmc.TableTr(
                [
                    dmc.TableTd(
                        dmc.Group(
                            gap="xs",
                            wrap="nowrap",
                            children=[
                                dmc.Box(
                                    w=8,
                                    h=8,
                                    bg=COLORS[index % len(COLORS)],
                                    style={"borderRadius": "999px", "flex": "0 0 auto"},
                                ),
                                dmc.Text(country["country"], size="sm", fw=600),
                            ],
                        )
                    ),
                    *value_cells,
                ]
            )
        )

    return dmc.ScrollArea(
        scrollbars="x",
        type="always",
        offsetScrollbars="x",
        w="100%",
        children=dmc.Table(
            miw=1160,
            withTableBorder=True,
            withColumnBorders=True,
            striped=True,
            highlightOnHover=True,
            tabularNums=True,
            verticalSpacing="sm",
            children=[
                dmc.TableThead(dmc.TableTr(header_cells)),
                dmc.TableTbody(rows),
            ],
        ),
    )


def _world_bank_comparison_section(payload: dict[str, Any]) -> dmc.Box:
    series = payload["series"]
    available_contexts = [
        country["world_bank_context"]
        for country in series
        if country.get("world_bank_context")
    ]
    unavailable_countries = [
        country["country"]
        for country in series
        if not country.get("world_bank_context")
    ]
    heading = dmc.Group(
        justify="space-between",
        align="center",
        mb="xs",
        children=[
            dmc.Title("World Bank baseline comparison", order=3),
            dmc.Badge("Baseline context", variant="light", color="blue"),
        ],
    )
    if not available_contexts:
        return dmc.Box(
            mt="xl",
            children=[
                heading,
                create_alert(
                    "World Bank context is unavailable for the selected countries "
                    "or active snapshot.",
                    "warning",
                ),
            ],
        )

    selected_definition = world_bank_metric(DEFAULT_WORLD_BANK_COMPARISON_METRIC)
    return dmc.Box(
        mt="xl",
        children=[
            heading,
            dmc.Text(
                "Compare source-faithful country baselines without treating them "
                "as causes of COVID outcomes.",
                size="sm",
                c="dimmed",
                mb="md",
            ),
            (
                create_alert(
                    "World Bank context is unavailable for: "
                    + ", ".join(unavailable_countries)
                    + ".",
                    "warning",
                )
                if unavailable_countries
                else None
            ),
            dmc.Select(
                id="comparison-world-bank-metric",
                label="World Bank metric",
                data=[
                    {
                        "label": definition.selector_label,
                        "value": definition.key,
                    }
                    for definition in WORLD_BANK_BASELINE_METRICS
                ],
                value=DEFAULT_WORLD_BANK_COMPARISON_METRIC,
                clearable=False,
                w={"base": "100%", "sm": 360},
                mb="md",
            ),
            create_chart_card(
                world_bank_comparison_figure(series, selected_definition),
                graph_id="comparison-world-bank-chart",
            ),
            dmc.Title("Exact baseline values", order=4, mt="lg", mb="sm"),
            dmc.Paper(
                p="md",
                radius="md",
                withBorder=True,
                children=_world_bank_comparison_table(series),
            ),
            dmc.Text(
                "WDI population is context only; it is not the frozen COVID rate "
                "denominator.",
                size="xs",
                c="dimmed",
                mt="sm",
            ),
            dmc.Box(
                mt="md",
                children=create_alert(
                    "These baseline indicators are descriptive country context. "
                    "They do not establish causes of COVID outcomes.",
                    "info",
                ),
            ),
            dmc.Divider(mt="md", mb="sm"),
            dmc.Text(
                "World Development Indicators · Snapshot: "
                f"{available_contexts[0]['snapshot_id']}",
                size="xs",
                c="dimmed",
            ),
        ],
    )


@callback(
    Output("comparison-page-data", "data"),
    Input("comparison-countries", "value", allow_optional=True),
    Input("compare-start-date", "value", allow_optional=True),
    Input("compare-end-date", "value", allow_optional=True),
    Input("comparison-retry", "n_clicks", allow_optional=True),
    State("country-catalog", "data"),
    prevent_initial_call=True,
    running=[(Output("comparison-loading", "visible"), True, False)],
)
def load_comparison_page(
    countries: list[str] | None,
    start_date: str | date | None,
    end_date: str | date | None,
    _retry_clicks: int | None,
    catalog_state: dict[str, Any] | None,
) -> dict[str, Any]:
    if not catalog_state:
        return {"state": "catalog_loading"}
    if catalog_state.get("state") == "error":
        return {
            "state": "upstream_error",
            "message": catalog_state.get("message"),
            "code": catalog_state.get("code"),
            "request_id": catalog_state.get("request_id"),
        }
    if not catalog_state.get("payload"):
        return {"state": "empty_catalog"}
    if not countries:
        return {"state": "neutral"}
    if not 2 <= len(countries) <= 10:
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
    if not state or state.get("state") in {"neutral", "catalog_loading"}:
        return status_badge("Waiting", "neutral", "Choose comparison filters")
    if state.get("state") == "upstream_error":
        return html.Div()
    if state.get("state") == "empty_catalog":
        return status_badge(
            "Setup incomplete",
            "error",
            "No countries are available. Complete the Snowflake setup and retry.",
        )
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
            _world_bank_comparison_section(payload),
        ]
    )


@callback(
    Output("comparison-world-bank-chart", "figure"),
    Input("comparison-world-bank-metric", "value", allow_optional=True),
    State("comparison-page-data", "data"),
    prevent_initial_call=True,
)
def update_world_bank_comparison_chart(
    metric_key: str | None,
    state: dict[str, Any] | None,
):
    if not state or state.get("state") != "success":
        raise PreventUpdate
    # The selector reuses the page store. Keeping it independent from the loader
    # prevents a presentation-only choice from issuing another API or warehouse call.
    return world_bank_comparison_figure(
        state["payload"]["series"],
        world_bank_metric(metric_key),
    )


@callback(
    Output("forecast-page-data", "data"),
    Input("forecast-country", "value", allow_optional=True),
    Input("forecast-metric", "value", allow_optional=True),
    Input("forecast-horizon", "value", allow_optional=True),
    Input("forecast-lookback", "value", allow_optional=True),
    Input("forecast-retry", "n_clicks", allow_optional=True),
    prevent_initial_call=True,
    running=[(Output("forecast-loading", "visible"), True, False)],
)
def load_forecast_page(
    identifier: str | None,
    metric: str | None,
    horizon_days: str | int | None,
    lookback_days: str | int | None,
    _retry_clicks: int | None,
) -> dict[str, Any]:
    if not identifier or not metric or not horizon_days or not lookback_days:
        return {
            "state": "error",
            "message": "Choose a country, metric, horizon, and training window.",
        }
    try:
        horizon = int(horizon_days)
        lookback = int(lookback_days)
    except (TypeError, ValueError):
        return {"state": "error", "message": "Use valid forecast settings."}
    if not 1 <= horizon <= 30 or not 42 <= lookback <= 180:
        return {"state": "error", "message": "Forecast settings are out of range."}

    try:
        return _success(
            get_json(
                settings.dashboard_api_base_url,
                "/forecast",
                params={
                    "country": identifier,
                    "metric": metric,
                    "days": str(horizon),
                    "lookback_days": str(lookback),
                },
            )
        )
    except DashboardApiError as exc:
        return _error(exc)


@callback(Output("forecast-content", "children"), Input("forecast-page-data", "data"))
def render_forecast_content(state: dict[str, Any] | None) -> html.Div:
    if not state:
        return status_badge("Waiting", "neutral", "Choose forecast settings")
    if state.get("state") == "error":
        return create_alert(state["message"])

    payload = state["payload"]
    evaluation = payload["evaluation"]
    model_label = evaluation["selected_model"].replace("_", " ").title()
    metric_label = payload["metric"].replace("_", " ").title()
    caveats = payload.get("caveats", [])
    return dmc.Box(
        [
            dmc.Grid(
                children=[
                    dmc.GridCol(
                        span={"base": 12, "sm": 6, "lg": 3},
                        children=create_kpi_card(
                            "Selected model",
                            model_label,
                            "tabler:binary-tree",
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "sm": 6, "lg": 3},
                        children=create_kpi_card(
                            "Holdout MAE",
                            f"{evaluation['selected_mae']:,.1f}",
                            "tabler:chart-dots",
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "sm": 6, "lg": 3},
                        children=create_kpi_card(
                            "Holdout RMSE",
                            f"{evaluation['selected_rmse']:,.1f}",
                            "tabler:chart-histogram",
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "sm": 6, "lg": 3},
                        children=create_kpi_card(
                            "Training observations",
                            _format_integer(payload["training_observations"]),
                            "tabler:calendar-stats",
                        ),
                    ),
                ],
                mb="md",
            ),
            create_chart_card(
                forecast_figure(
                    payload["history"],
                    payload["forecast"],
                    f"{payload['country']}: {metric_label} forecast",
                )
            ),
            dmc.Paper(
                p="md",
                radius="md",
                withBorder=True,
                mt="md",
                children=[
                    dmc.Text("Temporal validation", fw=600, mb="xs"),
                    dmc.Text(
                        "The last "
                        f"{evaluation['holdout_observations']} observations from "
                        f"{evaluation['holdout_start_date']} were predicted in order. "
                        "The lower-MAE candidate was then refit to the selected "
                        "history before forecasting.",
                        size="sm",
                    ),
                    dmc.Text(
                        "7-day mean: "
                        f"MAE {evaluation['moving_average_mae']:,.1f}, "
                        f"RMSE {evaluation['moving_average_rmse']:,.1f} | "
                        "Linear trend: "
                        f"MAE {evaluation['linear_trend_mae']:,.1f}, "
                        f"RMSE {evaluation['linear_trend_rmse']:,.1f}",
                        size="sm",
                        c="dimmed",
                        mt="xs",
                    ),
                ],
            ),
            (
                create_alert(
                    html.Ul([html.Li(caveat) for caveat in caveats]),
                    "warning",
                )
                if caveats
                else None
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
