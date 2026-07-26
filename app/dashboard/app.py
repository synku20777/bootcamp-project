from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from dash import Dash, Input, Output, State, callback, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate

from app.config import get_dashboard_settings
from app.dashboard.api_client import DashboardApiError, get_json, post_json
from app.dashboard.charts import (
    COLORS,
    comparison_figure,
    metric_figure,
    overview_bar,
    overview_map,
)
from app.dashboard.layouts import (
    alert,
    annotation_page,
    chart_card,
    comparison_page,
    country_page,
    kpi,
    not_found_page,
    overview_page,
    status_badge,
    status_page,
)
from app.logging_config import configure_logging

settings = get_dashboard_settings()
logger = logging.getLogger(__name__)
assets_directory = Path(__file__).with_name("assets")

app = Dash(
    __name__,
    title="COVID-19 Analytics Platform",
    assets_folder=str(assets_directory),
    suppress_callback_exceptions=True,
)
server = app.server


def _navigation() -> html.Nav:
    return html.Nav(
        [
            dcc.Link("Status", href="/", className="nav-link"),
            dcc.Link("Overview", href="/overview", className="nav-link"),
            dcc.Link("Country Explorer", href="/country", className="nav-link"),
            dcc.Link("Comparison", href="/compare", className="nav-link"),
            dcc.Link("Annotations", href="/annotations", className="nav-link"),
        ],
        className="main-nav",
        **{"aria-label": "Main navigation"},
    )


app.layout = html.Div(
    [
        dcc.Location(id="page-location"),
        dcc.Store(id="snowflake-status", storage_type="session"),
        dcc.Store(id="country-catalog", storage_type="session"),
        dcc.Store(id="overview-page-data", storage_type="memory"),
        dcc.Store(id="country-page-data", storage_type="memory"),
        dcc.Store(id="comparison-page-data", storage_type="memory"),
        dcc.Store(id="annotation-page-data", storage_type="memory"),
        html.Header(
            [
                dcc.Link(
                    "COVID / DATA",
                    href="/",
                    className="brand",
                ),
                _navigation(),
            ],
            className="site-header",
        ),
        html.Main(id="page-content", className="page-shell"),
    ]
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
        return alert(state["message"])

    payload = state["payload"]
    totals = payload["totals"]
    locations = payload["locations"]
    return html.Div(
        [
            html.Section(
                [
                    kpi("Countries", _format_integer(totals["countries"])),
                    kpi("Total cases", _format_integer(totals["total_cases"])),
                    kpi("Total deaths", _format_integer(totals["total_deaths"])),
                    kpi(
                        "Mortality rate",
                        _format_rate(totals["mortality_rate_percent"]),
                    ),
                ],
                className="kpi-grid",
            ),
            html.P(
                f"Latest reporting date: {totals['report_date']}",
                className="data-note",
            ),
            html.Section(
                [
                    chart_card(
                        overview_bar(
                            locations,
                            "cases_cumulative",
                            "Top 10 countries by cases",
                            COLORS[0],
                        )
                    ),
                    chart_card(
                        overview_bar(
                            locations,
                            "deaths_cumulative",
                            "Top 10 countries by deaths",
                            COLORS[2],
                        )
                    ),
                    html.Article(
                        dcc.Graph(
                            figure=overview_map(locations),
                            config={"displayModeBar": False},
                            responsive=True,
                        ),
                        className="chart-card chart-card--wide",
                    ),
                ],
                className="chart-grid",
            ),
        ]
    )


@callback(
    Output("country-page-data", "data"),
    Input("country-select", "value", allow_optional=True),
    Input("country-metric", "value", allow_optional=True),
    Input("country-date-range", "start_date", allow_optional=True),
    Input("country-date-range", "end_date", allow_optional=True),
    Input("country-retry", "n_clicks", allow_optional=True),
    prevent_initial_call=True,
)
def load_country_page(
    identifier: str | None,
    metric: str | None,
    start_date: str | None,
    end_date: str | None,
    _retry_clicks: int | None,
) -> dict[str, Any]:
    if not all((identifier, metric, start_date, end_date)):
        return {
            "state": "error",
            "message": "Choose a country, metric, and date range.",
        }
    try:
        payload = get_json(
            settings.dashboard_api_base_url,
            f"/dashboard/countries/{identifier}",
            params={
                "metric": metric,
                "start_date": start_date,
                "end_date": end_date,
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
        return alert(state["message"])

    payload = state["payload"]
    summary = payload["summary"]
    metric_title = payload["selected"]["metric"].replace("_", " ").title()
    return html.Div(
        [
            html.Div(
                [
                    html.H2(payload["country"]),
                    html.P(
                        " · ".join(
                            value
                            for value in (payload.get("iso2"), payload.get("iso3"))
                            if value
                        ),
                        className="identity-code",
                    ),
                ],
                className="identity-heading",
            ),
            html.Section(
                [
                    kpi("Population", _format_integer(summary["population"])),
                    kpi("Cases", _format_integer(summary["cases_cumulative"])),
                    kpi("Deaths", _format_integer(summary["deaths_cumulative"])),
                    kpi(
                        "Cases / 100k",
                        _format_integer(summary["cases_per_100k"]),
                    ),
                    kpi(
                        "Mortality",
                        _format_rate(summary["mortality_rate_percent"]),
                    ),
                ],
                className="kpi-grid",
            ),
            html.Section(
                [
                    chart_card(
                        metric_figure(
                            payload["selected"]["points"],
                            metric_title,
                            color=COLORS[0],
                        )
                    ),
                    chart_card(
                        metric_figure(
                            payload["daily_cases"]["points"],
                            "Daily cases",
                            color=COLORS[1],
                            chart_type="bar",
                        )
                    ),
                    chart_card(
                        metric_figure(
                            payload["daily_deaths"]["points"],
                            "Daily deaths",
                            color=COLORS[2],
                            chart_type="bar",
                        )
                    ),
                    chart_card(
                        metric_figure(
                            payload["mortality"]["points"],
                            "Mortality rate (%)",
                            color=COLORS[3],
                        )
                    ),
                ],
                className="chart-grid",
            ),
        ]
    )


@callback(
    Output("comparison-page-data", "data"),
    Input("comparison-countries", "value", allow_optional=True),
    Input("comparison-date-range", "start_date", allow_optional=True),
    Input("comparison-date-range", "end_date", allow_optional=True),
    Input("comparison-retry", "n_clicks", allow_optional=True),
    prevent_initial_call=True,
)
def load_comparison_page(
    countries: list[str] | None,
    start_date: str | None,
    end_date: str | None,
    _retry_clicks: int | None,
) -> dict[str, Any]:
    if not countries or not 2 <= len(countries) <= 10:
        return {"state": "error", "message": "Choose between 2 and 10 countries."}
    if not start_date or not end_date:
        return {"state": "error", "message": "Choose a complete date range."}

    params = [("country", country) for country in countries]
    params.extend((("start_date", start_date), ("end_date", end_date)))
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
        return alert(state["message"])

    payload = state["payload"]
    missing = payload["countries_without_data"]
    return html.Div(
        [
            (
                alert(
                    "No observations for: " + ", ".join(missing),
                    state="warning",
                )
                if missing
                else None
            ),
            html.Section(
                [
                    chart_card(
                        comparison_figure(
                            payload["series"],
                            "cases_per_100k",
                            "Cases per 100,000",
                        )
                    ),
                    chart_card(
                        comparison_figure(
                            payload["series"],
                            "deaths_per_100k",
                            "Deaths per 100,000",
                        )
                    ),
                    html.Article(
                        dcc.Graph(
                            figure=comparison_figure(
                                payload["series"],
                                "mortality",
                                "Mortality rate (%)",
                            ),
                            config={"displayModeBar": False},
                            responsive=True,
                        ),
                        className="chart-card chart-card--wide",
                    ),
                ],
                className="chart-grid",
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
    Input("annotation-filter-dates", "start_date", allow_optional=True),
    Input("annotation-filter-dates", "end_date", allow_optional=True),
    Input("annotation-retry", "n_clicks", allow_optional=True),
    Input("annotation-submit", "n_clicks", allow_optional=True),
    State("annotation-report-date", "date", allow_optional=True),
    State("annotation-metric", "value", allow_optional=True),
    State("annotation-created-by", "value", allow_optional=True),
    State("annotation-comment", "value", allow_optional=True),
    State("annotation-page-data", "data"),
    prevent_initial_call=True,
)
def update_annotation_page(
    country: str | None,
    filter_metric: str | None,
    filter_start_date: str | None,
    filter_end_date: str | None,
    _retry_clicks: int | None,
    _submit_clicks: int | None,
    report_date: str | None,
    metric: str | None,
    created_by: str | None,
    comment: str | None,
    current: dict[str, Any] | None,
) -> dict[str, Any]:
    if not country:
        return {"state": "error", "message": "Choose a country."}

    try:
        if ctx.triggered_id != "annotation-submit":
            return _load_annotations(
                country,
                filter_metric,
                filter_start_date,
                filter_end_date,
            )

        if not all((report_date, metric, created_by, comment)):
            return {
                "state": "error",
                "message": "Complete the report date, metric, name, and comment.",
            }
        created = post_json(
            settings.dashboard_api_base_url,
            "/annotations",
            payload={
                "country": country,
                "report_date": report_date,
                "metric": metric,
                "created_by": created_by,
                "comment": comment,
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
            filter_start_date,
            filter_end_date,
        ):
            existing.append(created)
            existing.sort(key=lambda item: (item["report_date"], item["created_at"]))
        return {
            "state": "success",
            "payload": existing,
            "notice": "Annotation saved.",
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
        return alert(state["message"])

    annotations = state["payload"]
    records = [
        html.Article(
            [
                html.Div(
                    [
                        html.Strong(annotation["country"]),
                        html.Span(
                            annotation["metric"].replace("_", " ").title(),
                            className="annotation-metric",
                        ),
                    ],
                    className="annotation-heading",
                ),
                html.P(annotation["comment"], className="annotation-text"),
                html.P(
                    f"{annotation['report_date']} · {annotation['created_by']}",
                    className="annotation-meta",
                ),
            ],
            className="annotation-card",
        )
        for annotation in annotations
    ]
    return html.Div(
        [
            alert(state["notice"], state="success") if state.get("notice") else None,
            html.H2("Saved annotations"),
            (
                html.Div(records, className="annotation-list")
                if records
                else status_badge(
                    "No annotations",
                    "neutral",
                    "Nothing matches these filters yet",
                )
            ),
        ],
        className="annotation-results",
    )


@callback(Output("api-live-status", "children"), Input("page-location", "pathname"))
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
    running=[(Output("check-snowflake", "disabled"), True, False)],
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
