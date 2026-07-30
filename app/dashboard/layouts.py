from __future__ import annotations

from datetime import date
from typing import Any

import dash_mantine_components as dmc
from dash import html
from dash_iconify import DashIconify

from app.dashboard.components import (
    create_alert,
    create_loading_state,
    create_page_header,
)
from app.models.covid import ForecastMetric, Metric

DEFAULT_START_DATE = date(2020, 3, 1)
DEFAULT_END_DATE = date(2023, 3, 9)
ERROR_STATE = "error"
NEUTRAL_STATE = "neutral"
RETRY_DATA_LABEL = "Retry data"


def status_badge(label: str, state: str, detail: str) -> dmc.Alert:
    color_map = {"error": "red", "success": "teal", "neutral": "gray"}
    icon_map = {
        "error": "tabler:alert-circle",
        "success": "tabler:check",
        "neutral": "tabler:clock",
    }
    return dmc.Alert(
        detail,
        title=label,
        color=color_map.get(state, "gray"),
        icon=DashIconify(icon=icon_map.get(state, "tabler:info-circle"), width=20),
        variant="light",
    )


def status_page(public_api_base_url: str) -> html.Div:
    return html.Div(
        [
            create_page_header(
                "Backend foundation status",
                "Cheap process checks are automatic. Snowflake is checked only when you request it.",
            ),
            dmc.Grid(
                children=[
                    dmc.GridCol(
                        span={"base": 12, "md": 4},
                        children=dmc.Paper(
                            p="md",
                            radius="md",
                            withBorder=True,
                            children=[
                                dmc.Text("FastAPI", fw=600, mb="sm"),
                                create_loading_state(
                                    html.Div(
                                        status_badge(
                                            "Checking",
                                            NEUTRAL_STATE,
                                            "Confirming process liveness",
                                        ),
                                        id="api-live-status",
                                    ),
                                    loading_id="api-live-loading",
                                ),
                                dmc.Anchor(
                                    "Open Swagger documentation",
                                    href=f"{public_api_base_url}/docs",
                                    target="_blank",
                                    size="sm",
                                    mt="md",
                                    display="block",
                                ),
                            ],
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "md": 4},
                        children=dmc.Paper(
                            p="md",
                            radius="md",
                            withBorder=True,
                            children=[
                                dmc.Text("Local dependencies", fw=600, mb="sm"),
                                create_loading_state(
                                    html.Div(
                                        status_badge(
                                            "Checking",
                                            NEUTRAL_STATE,
                                            "Waiting for Redis and MongoDB",
                                        ),
                                        id="api-ready-status",
                                    ),
                                    loading_id="api-ready-loading",
                                ),
                                dmc.Text(
                                    "Readiness does not connect to Snowflake.",
                                    size="xs",
                                    c="dimmed",
                                    mt="md",
                                ),
                            ],
                        ),
                    ),
                    dmc.GridCol(
                        span={"base": 12, "md": 4},
                        children=dmc.Paper(
                            p="md",
                            radius="md",
                            withBorder=True,
                            children=[
                                dmc.Text("Snowflake", fw=600, mb="sm"),
                                create_loading_state(
                                    html.Div(
                                        status_badge(
                                            "Not checked",
                                            NEUTRAL_STATE,
                                            "No warehouse query made",
                                        ),
                                        id="snowflake-status-view",
                                    ),
                                    loading_id="snowflake-loading",
                                ),
                                dmc.Button(
                                    "Check Snowflake",
                                    id="check-snowflake",
                                    n_clicks=0,
                                    variant="light",
                                    color="teal",
                                    mt="md",
                                ),
                                dmc.Text(
                                    "This explicit check may resume COVID_WH. The result is kept for this browser session.",
                                    size="xs",
                                    c="dimmed",
                                    mt="xs",
                                ),
                            ],
                        ),
                    ),
                ]
            ),
        ]
    )


def overview_page() -> html.Div:
    return html.Div(
        [
            dmc.Group(
                justify="space-between",
                align="flex-start",
                children=[
                    create_page_header(
                        "The pandemic at a glance",
                        "Latest cumulative outcomes and population-normalized impact.",
                    ),
                    dmc.Button(
                        RETRY_DATA_LABEL,
                        id="overview-retry",
                        n_clicks=0,
                        variant="light",
                        leftSection=DashIconify(icon="tabler:refresh", width=16),
                    ),
                ],
            ),
            create_loading_state(
                html.Div(id="overview-content"),
                loading_id="overview-loading",
            ),
        ]
    )


def patterns_page(catalog_state: dict[str, Any] | None) -> html.Div:
    options = _catalog_options(catalog_state)
    catalog_error = _catalog_error(catalog_state)
    return html.Div(
        [
            create_page_header(
                "Sustained reporting patterns",
                "Explore uninterrupted runs of increasing reported daily cases.",
            ),
            catalog_error,
            dmc.Paper(
                p="md",
                radius="md",
                withBorder=True,
                mb="lg",
                children=dmc.Group(
                    align="flex-end",
                    children=[
                        dmc.Select(
                            id="patterns-country",
                            label="Country",
                            placeholder="All countries",
                            data=options,
                            value=None,
                            clearable=True,
                            searchable=True,
                            disabled=not options,
                            w=220,
                        ),
                        dmc.DateInput(
                            id="patterns-start-date",
                            label="Start Date",
                            value=DEFAULT_START_DATE,
                            valueFormat="YYYY-MM-DD",
                            minDate=DEFAULT_START_DATE,
                            maxDate=DEFAULT_END_DATE,
                            w=150,
                        ),
                        dmc.DateInput(
                            id="patterns-end-date",
                            label="End Date",
                            value=DEFAULT_END_DATE,
                            valueFormat="YYYY-MM-DD",
                            minDate=DEFAULT_START_DATE,
                            maxDate=DEFAULT_END_DATE,
                            w=150,
                        ),
                        dmc.Select(
                            id="patterns-minimum-increases",
                            label="Minimum increases",
                            data=[
                                {
                                    "label": f"{value} increases",
                                    "value": str(value),
                                }
                                for value in (3, 5, 7, 10, 14)
                            ],
                            value="3",
                            clearable=False,
                            w=175,
                        ),
                        dmc.Button(
                            RETRY_DATA_LABEL,
                            id="patterns-retry",
                            n_clicks=0,
                            variant="light",
                            leftSection=DashIconify(icon="tabler:refresh", width=16),
                        ),
                    ],
                ),
            ),
            create_alert(
                "These are uninterrupted sequences in reported daily cases. They "
                "describe reporting patterns, not epidemiological regimes or "
                "causal changes in transmission.",
                "info",
            ),
            create_loading_state(
                html.Div(id="patterns-content"),
                loading_id="patterns-loading",
            ),
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
    if not catalog_state:
        return html.Div(
            status_badge(
                "Loading countries",
                NEUTRAL_STATE,
                "Connecting to the analytics API",
            ),
            style={"marginBottom": "16px"},
        )
    if catalog_state.get("state") == "success" and catalog_state.get("payload"):
        return None
    is_error = catalog_state.get("state") == "error"
    message = (
        catalog_state.get("message", "The country catalogue is unavailable.")
        if is_error
        else "The API returned an empty country catalogue. Complete setup and retry."
    )
    request_id = catalog_state.get("request_id")
    alert_children: list[Any] = [dmc.Text(message)]
    if request_id:
        alert_children.append(dmc.Text(f"Request ID: {request_id}", size="xs", mt="xs"))
    return html.Div(
        [
            create_alert(dmc.Stack(alert_children, gap=0), "error"),
            dmc.Button(
                "Retry countries",
                id="retry-catalog",
                n_clicks=0,
                variant="light",
                color="red",
                mt="md",
            ),
        ],
        style={"marginBottom": "16px"},
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
            create_page_header(
                "Country Explorer",
                "Every KPI and chart below is rendered from one page-level store.",
            ),
            catalog_error,
            dmc.Paper(
                p="md",
                radius="md",
                withBorder=True,
                mb="lg",
                children=dmc.Group(
                    align="flex-end",
                    children=[
                        dmc.Select(
                            id="country-select",
                            label="Country",
                            data=options,
                            value=_default_country(options, "LV"),
                            clearable=False,
                            disabled=not options,
                            w=200,
                        ),
                        dmc.Select(
                            id="country-metric",
                            label="Metric",
                            data=[
                                {
                                    "label": metric.value.replace("_", " ").title(),
                                    "value": metric.value,
                                }
                                for metric in Metric
                            ],
                            value=Metric.CASES_PER_100K.value,
                            clearable=False,
                            w=200,
                        ),
                        dmc.DateInput(
                            id="country-start-date",
                            label="Start Date",
                            value=DEFAULT_START_DATE,
                            valueFormat="YYYY-MM-DD",
                            minDate=DEFAULT_START_DATE,
                            maxDate=DEFAULT_END_DATE,
                            w=150,
                        ),
                        dmc.DateInput(
                            id="country-end-date",
                            label="End Date",
                            value=DEFAULT_END_DATE,
                            valueFormat="YYYY-MM-DD",
                            minDate=DEFAULT_START_DATE,
                            maxDate=DEFAULT_END_DATE,
                            w=150,
                        ),
                        dmc.Button(
                            RETRY_DATA_LABEL,
                            id="country-retry",
                            n_clicks=0,
                            variant="light",
                            leftSection=DashIconify(icon="tabler:refresh", width=16),
                        ),
                    ],
                ),
            ),
            create_loading_state(
                html.Div(id="country-content"),
                loading_id="country-loading",
            ),
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
            create_page_header(
                "Country Comparison",
                "Three analytical views share one ordered comparison payload.",
            ),
            catalog_error,
            dmc.Paper(
                p="md",
                radius="md",
                withBorder=True,
                mb="lg",
                children=dmc.Group(
                    align="flex-end",
                    children=[
                        dmc.MultiSelect(
                            id="comparison-countries",
                            label="Countries (2-10)",
                            data=options,
                            value=defaults,
                            disabled=not options,
                            w=300,
                            maxValues=10,
                        ),
                        dmc.DateInput(
                            id="compare-start-date",
                            label="Start Date",
                            value=DEFAULT_START_DATE,
                            valueFormat="YYYY-MM-DD",
                            minDate=DEFAULT_START_DATE,
                            maxDate=DEFAULT_END_DATE,
                            w=150,
                        ),
                        dmc.DateInput(
                            id="compare-end-date",
                            label="End Date",
                            value=DEFAULT_END_DATE,
                            valueFormat="YYYY-MM-DD",
                            minDate=DEFAULT_START_DATE,
                            maxDate=DEFAULT_END_DATE,
                            w=150,
                        ),
                        dmc.Button(
                            RETRY_DATA_LABEL,
                            id="comparison-retry",
                            n_clicks=0,
                            disabled=not options,
                            variant="light",
                            leftSection=DashIconify(icon="tabler:refresh", width=16),
                        ),
                    ],
                ),
            ),
            create_loading_state(
                html.Div(id="comparison-content"),
                loading_id="comparison-loading",
            ),
        ]
    )


def forecast_page(catalog_state: dict[str, Any] | None) -> html.Div:
    options = _catalog_options(catalog_state)
    catalog_error = _catalog_error(catalog_state)
    return html.Div(
        [
            create_page_header(
                "Forecasting",
                "Compare transparent baselines using temporal holdout error.",
            ),
            catalog_error,
            dmc.Paper(
                p="md",
                radius="md",
                withBorder=True,
                mb="lg",
                children=dmc.Group(
                    align="flex-end",
                    children=[
                        dmc.Select(
                            id="forecast-country",
                            label="Country",
                            data=options,
                            value=_default_country(options, "LV"),
                            clearable=False,
                            disabled=not options,
                            w=200,
                        ),
                        dmc.Select(
                            id="forecast-metric",
                            label="Metric",
                            data=[
                                {
                                    "label": metric.value.replace("_", " ").title(),
                                    "value": metric.value,
                                }
                                for metric in ForecastMetric
                            ],
                            value=ForecastMetric.NEW_CASES.value,
                            clearable=False,
                            w=180,
                        ),
                        dmc.Select(
                            id="forecast-horizon",
                            label="Forecast horizon",
                            data=[
                                {"label": f"{days} days", "value": str(days)}
                                for days in (7, 14, 21, 30)
                            ],
                            value="30",
                            clearable=False,
                            w=160,
                        ),
                        dmc.Select(
                            id="forecast-lookback",
                            label="Training window",
                            data=[
                                {"label": f"{days} days", "value": str(days)}
                                for days in (42, 60, 90, 120, 180)
                            ],
                            value="90",
                            clearable=False,
                            w=160,
                        ),
                        dmc.Button(
                            RETRY_DATA_LABEL,
                            id="forecast-retry",
                            n_clicks=0,
                            variant="light",
                            leftSection=DashIconify(icon="tabler:refresh", width=16),
                        ),
                    ],
                ),
            ),
            create_loading_state(
                html.Div(id="forecast-content"),
                loading_id="forecast-loading",
            ),
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
            create_page_header(
                "Annotations",
                "Comments are validated against a real country and reporting date.",
            ),
            catalog_error,
            dmc.Paper(
                p="md",
                radius="md",
                withBorder=True,
                mb="lg",
                children=dmc.Group(
                    align="flex-end",
                    children=[
                        dmc.Select(
                            id="annotation-country",
                            label="Country",
                            data=options,
                            value=default_country,
                            clearable=False,
                            disabled=not options,
                            w=200,
                        ),
                        dmc.Select(
                            id="annotation-filter-metric",
                            label="Filter metric",
                            data=[{"label": "All metrics", "value": ""}]
                            + metric_options,
                            value="",
                            clearable=False,
                            w=200,
                        ),
                        dmc.DateInput(
                            id="annotation-filter-start-date",
                            label="Filter Start Date",
                            value=DEFAULT_START_DATE,
                            valueFormat="YYYY-MM-DD",
                            minDate=DEFAULT_START_DATE,
                            maxDate=DEFAULT_END_DATE,
                            w=150,
                            clearable=True,
                        ),
                        dmc.DateInput(
                            id="annotation-filter-end-date",
                            label="Filter End Date",
                            value=DEFAULT_END_DATE,
                            valueFormat="YYYY-MM-DD",
                            minDate=DEFAULT_START_DATE,
                            maxDate=DEFAULT_END_DATE,
                            w=150,
                            clearable=True,
                        ),
                        dmc.Button(
                            "Retry list",
                            id="annotation-retry",
                            n_clicks=0,
                            variant="light",
                            leftSection=DashIconify(icon="tabler:refresh", width=16),
                        ),
                    ],
                ),
            ),
            dmc.Paper(
                p="md",
                radius="md",
                withBorder=True,
                mb="lg",
                children=dmc.Stack(
                    [
                        dmc.Title("Add an annotation", order=3),
                        dmc.Group(
                            [
                                dmc.DateInput(
                                    id="annotation-report-date",
                                    label="Report date",
                                    value=DEFAULT_END_DATE,
                                    valueFormat="YYYY-MM-DD",
                                    minDate=DEFAULT_START_DATE,
                                    maxDate=DEFAULT_END_DATE,
                                    w=150,
                                ),
                                dmc.Select(
                                    id="annotation-metric",
                                    label="Metric",
                                    data=metric_options,
                                    value=Metric.NEW_CASES.value,
                                    clearable=False,
                                    w=200,
                                ),
                                dmc.TextInput(
                                    id="annotation-created-by",
                                    label="Display name",
                                    placeholder="Your name",
                                    inputProps={"minLength": 1, "maxLength": 80},
                                    w=200,
                                ),
                            ]
                        ),
                        dmc.Textarea(
                            id="annotation-comment",
                            label="Comment",
                            placeholder="Add context for this data point...",
                            inputProps={"minLength": 1, "maxLength": 1000},
                            autosize=True,
                            minRows=2,
                        ),
                        dmc.Button(
                            "Save annotation",
                            id="annotation-submit",
                            n_clicks=0,
                        ),
                    ]
                ),
            ),
            create_loading_state(
                html.Div(id="annotation-content"),
                loading_id="annotation-loading",
            ),
        ]
    )


def not_found_page() -> html.Div:
    return html.Div(
        [
            create_page_header(
                "404 - Page not found",
                "Use the navigation to return to an available dashboard page.",
            )
        ]
    )
