from __future__ import annotations

from datetime import date
from typing import Any

from dash import dcc, html

from app.models.covid import Metric

DEFAULT_START_DATE = date(2020, 3, 1)
DEFAULT_END_DATE = date(2020, 12, 14)
DATE_DISPLAY_FORMAT = "YYYY-MM-DD"
ERROR_STATE = "error"
NEUTRAL_STATE = "neutral"
LOADING_TYPE = "circle"
CONTROL_CLASS = "control"
WIDE_CONTROL_CLASS = "control control--wide"
CONTROL_PANEL_CLASS = "control-panel"
CONTROL_BUTTON_CLASS = "secondary-button control-button"
RETRY_DATA_LABEL = "Retry data"


def status_badge(label: str, state: str, detail: str) -> html.Div:
    return html.Div(
        [
            html.Span(className=f"status-dot status-dot--{state}"),
            html.Div(
                [
                    html.Strong(label),
                    html.Span(detail, className="status-detail"),
                ]
            ),
        ],
        className=f"status-badge status-badge--{state}",
        role="status",
        **{"aria-live": "polite"},
    )


def alert(message: str, state: str = ERROR_STATE) -> html.Div:
    return html.Div(
        message,
        className=f"alert alert--{state}",
        role="alert" if state == ERROR_STATE else "status",
    )


def page_heading(eyebrow: str, title: str, description: str) -> html.Header:
    return html.Header(
        [
            html.P(eyebrow, className="eyebrow"),
            html.H1(title),
            html.P(description, className="subtitle"),
        ],
        className="page-heading",
    )


def loading_panel(component_id: str) -> dcc.Loading:
    return dcc.Loading(
        html.Div(
            status_badge("Loading", NEUTRAL_STATE, "Waiting for page data"),
            id=component_id,
            **{"aria-live": "polite"},
        ),
        type=LOADING_TYPE,
    )


def status_page(public_api_base_url: str) -> html.Div:
    return html.Div(
        [
            page_heading(
                "COVID-19 ANALYTICS PLATFORM",
                "Backend foundation status",
                "Cheap process checks are automatic. Snowflake is checked only "
                "when you request it.",
            ),
            html.Section(
                [
                    html.Article(
                        [
                            html.H2("FastAPI"),
                            dcc.Loading(
                                html.Div(
                                    status_badge(
                                        "Checking",
                                        NEUTRAL_STATE,
                                        "Confirming process liveness",
                                    ),
                                    id="api-live-status",
                                ),
                                type=LOADING_TYPE,
                            ),
                            html.A(
                                "Open Swagger documentation",
                                href=f"{public_api_base_url}/docs",
                                target="_blank",
                                className="link-button",
                            ),
                        ],
                        className="status-card",
                    ),
                    html.Article(
                        [
                            html.H2("Snowflake"),
                            dcc.Loading(
                                html.Div(
                                    status_badge(
                                        "Not checked",
                                        NEUTRAL_STATE,
                                        "No warehouse query made",
                                    ),
                                    id="snowflake-status-view",
                                ),
                                type=LOADING_TYPE,
                            ),
                            html.Button(
                                "Check Snowflake",
                                id="check-snowflake",
                                n_clicks=0,
                                className="primary-button",
                            ),
                            html.P(
                                "This explicit check may resume COVID_WH. The "
                                "result is kept for this browser session.",
                                className="cost-note",
                            ),
                        ],
                        className="status-card",
                    ),
                ],
                className="status-grid",
            ),
        ]
    )


def overview_page() -> html.Div:
    return html.Div(
        [
            page_heading(
                "GLOBAL OVERVIEW",
                "The pandemic at a glance",
                "Latest cumulative outcomes and population-normalized impact.",
            ),
            html.Button(
                RETRY_DATA_LABEL,
                id="overview-retry",
                n_clicks=0,
                className="secondary-button",
            ),
            loading_panel("overview-content"),
        ]
    )


def _catalog_options(catalog_state: dict[str, Any] | None) -> list[dict[str, str]]:
    if not catalog_state or catalog_state.get("state") != "success":
        return []
    return [
        {
            "label": country["country"],
            "value": country.get("iso2")
            or country.get("iso3")
            or country["location_key"],
        }
        for country in catalog_state["payload"]
    ]


def _catalog_error(catalog_state: dict[str, Any] | None) -> html.Div | None:
    if not catalog_state or catalog_state.get("state") != "error":
        return None
    return html.Div(
        [
            alert(catalog_state["message"]),
            html.Button(
                "Retry countries",
                id="retry-catalog",
                n_clicks=0,
                className="secondary-button",
            ),
        ],
        className="state-panel",
    )


def _default_country(options: list[dict[str, str]], preferred: str) -> str | None:
    values = {option["value"] for option in options}
    if preferred in values:
        return preferred
    return options[0]["value"] if options else None


def country_page(catalog_state: dict[str, Any] | None) -> html.Div:
    options = _catalog_options(catalog_state)
    catalog_error = _catalog_error(catalog_state)
    return html.Div(
        [
            page_heading(
                "COUNTRY EXPLORER",
                "One country, one shared payload",
                "Every KPI and chart below is rendered from one page-level store.",
            ),
            catalog_error,
            html.Section(
                [
                    html.Label(
                        [
                            html.Span("Country"),
                            dcc.Dropdown(
                                id="country-select",
                                options=options,
                                value=_default_country(options, "LV"),
                                clearable=False,
                                disabled=not options,
                            ),
                        ],
                        className=CONTROL_CLASS,
                    ),
                    html.Label(
                        [
                            html.Span("Metric"),
                            dcc.Dropdown(
                                id="country-metric",
                                options=[
                                    {
                                        "label": metric.value.replace("_", " ").title(),
                                        "value": metric.value,
                                    }
                                    for metric in Metric
                                ],
                                value=Metric.CASES_PER_100K.value,
                                clearable=False,
                            ),
                        ],
                        className=CONTROL_CLASS,
                    ),
                    html.Label(
                        [
                            html.Span("Date range"),
                            dcc.DatePickerRange(
                                id="country-date-range",
                                start_date=DEFAULT_START_DATE,
                                end_date=DEFAULT_END_DATE,
                                display_format=DATE_DISPLAY_FORMAT,
                            ),
                        ],
                        className=WIDE_CONTROL_CLASS,
                    ),
                    html.Button(
                        RETRY_DATA_LABEL,
                        id="country-retry",
                        n_clicks=0,
                        className=CONTROL_BUTTON_CLASS,
                    ),
                ],
                className=CONTROL_PANEL_CLASS,
            ),
            loading_panel("country-content"),
        ]
    )


def comparison_page(catalog_state: dict[str, Any] | None) -> html.Div:
    options = _catalog_options(catalog_state)
    catalog_error = _catalog_error(catalog_state)
    defaults = [
        value
        for value in ("LV", "EE", "LT")
        if value in {item["value"] for item in options}
    ]
    if len(defaults) < 2:
        defaults = [item["value"] for item in options[:2]]
    return html.Div(
        [
            page_heading(
                "COUNTRY COMPARISON",
                "Compare normalized outcomes",
                "Three analytical views share one ordered comparison payload.",
            ),
            catalog_error,
            html.Section(
                [
                    html.Label(
                        [
                            html.Span("Countries (2–10)"),
                            dcc.Dropdown(
                                id="comparison-countries",
                                options=options,
                                value=defaults,
                                multi=True,
                                disabled=not options,
                            ),
                        ],
                        className=WIDE_CONTROL_CLASS,
                    ),
                    html.Label(
                        [
                            html.Span("Date range"),
                            dcc.DatePickerRange(
                                id="comparison-date-range",
                                start_date=DEFAULT_START_DATE,
                                end_date=DEFAULT_END_DATE,
                                display_format=DATE_DISPLAY_FORMAT,
                            ),
                        ],
                        className=WIDE_CONTROL_CLASS,
                    ),
                    html.Button(
                        RETRY_DATA_LABEL,
                        id="comparison-retry",
                        n_clicks=0,
                        className=CONTROL_BUTTON_CLASS,
                    ),
                ],
                className=CONTROL_PANEL_CLASS,
            ),
            loading_panel("comparison-content"),
        ]
    )


def annotation_page(catalog_state: dict[str, Any] | None) -> html.Div:
    options = _catalog_options(catalog_state)
    catalog_error = _catalog_error(catalog_state)
    default_country = _default_country(options, "LV")
    metric_options = [
        {
            "label": metric.value.replace("_", " ").title(),
            "value": metric.value,
        }
        for metric in Metric
    ]
    return html.Div(
        [
            page_heading(
                "ANNOTATIONS",
                "Add context to the data",
                "Comments are validated against a real country and reporting date.",
            ),
            catalog_error,
            html.Section(
                [
                    html.Label(
                        [
                            html.Span("Country"),
                            dcc.Dropdown(
                                id="annotation-country",
                                options=options,
                                value=default_country,
                                clearable=False,
                                disabled=not options,
                            ),
                        ],
                        className=CONTROL_CLASS,
                    ),
                    html.Label(
                        [
                            html.Span("Filter metric"),
                            dcc.Dropdown(
                                id="annotation-filter-metric",
                                options=[{"label": "All metrics", "value": ""}]
                                + metric_options,
                                value="",
                                clearable=False,
                            ),
                        ],
                        className=CONTROL_CLASS,
                    ),
                    html.Label(
                        [
                            html.Span("Filter dates"),
                            dcc.DatePickerRange(
                                id="annotation-filter-dates",
                                start_date=DEFAULT_START_DATE,
                                end_date=DEFAULT_END_DATE,
                                display_format=DATE_DISPLAY_FORMAT,
                                clearable=True,
                            ),
                        ],
                        className=WIDE_CONTROL_CLASS,
                    ),
                    html.Button(
                        "Retry list",
                        id="annotation-retry",
                        n_clicks=0,
                        className=CONTROL_BUTTON_CLASS,
                    ),
                ],
                className=CONTROL_PANEL_CLASS,
            ),
            html.Section(
                [
                    html.H2("Add an annotation"),
                    html.Div(
                        [
                            html.Label(
                                [
                                    html.Span("Report date"),
                                    dcc.DatePickerSingle(
                                        id="annotation-report-date",
                                        date=DEFAULT_END_DATE,
                                        display_format=DATE_DISPLAY_FORMAT,
                                    ),
                                ],
                                className=CONTROL_CLASS,
                            ),
                            html.Label(
                                [
                                    html.Span("Metric"),
                                    dcc.Dropdown(
                                        id="annotation-metric",
                                        options=metric_options,
                                        value=Metric.NEW_CASES.value,
                                        clearable=False,
                                    ),
                                ],
                                className=CONTROL_CLASS,
                            ),
                            html.Label(
                                [
                                    html.Span("Display name"),
                                    dcc.Input(
                                        id="annotation-created-by",
                                        type="text",
                                        minLength=1,
                                        maxLength=80,
                                        placeholder="Your name",
                                    ),
                                ],
                                className=CONTROL_CLASS,
                            ),
                        ],
                        className="annotation-form-grid",
                    ),
                    html.Label(
                        [
                            html.Span("Comment"),
                            dcc.Textarea(
                                id="annotation-comment",
                                minLength=1,
                                maxLength=1_000,
                                placeholder="Add context for this data point…",
                            ),
                        ],
                        className="control annotation-comment",
                    ),
                    html.Button(
                        "Save annotation",
                        id="annotation-submit",
                        n_clicks=0,
                        className="primary-button",
                    ),
                ],
                className="form-card",
            ),
            loading_panel("annotation-content"),
        ]
    )


def not_found_page() -> html.Div:
    return html.Div(
        [
            page_heading(
                "404",
                "Page not found",
                "Use the navigation to return to an available dashboard page.",
            )
        ]
    )


def kpi(label: str, value: str) -> html.Article:
    return html.Article(
        [html.Span(label, className="kpi-label"), html.Strong(value)],
        className="kpi-card",
    )


def chart_card(figure: Any) -> html.Article:
    return html.Article(
        dcc.Graph(figure=figure, config={"displayModeBar": False}, responsive=True),
        className="chart-card",
    )
