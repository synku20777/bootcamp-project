from __future__ import annotations

from datetime import date
from typing import Any

import dash_mantine_components as dmc
from dash import Input, Output, State, callback, html
from dash.exceptions import PreventUpdate

from app.dashboard.charts import (
    COLORS,
    comparison_figure,
    world_bank_comparison_figure,
)
from app.dashboard.components import create_alert, create_chart_card
from app.dashboard.layouts import status_badge
from app.dashboard.pages.common import (
    DashboardApiError,
    _error,
    _normalized_date_range,
    _success,
    get_json,
    settings,
)
from app.dashboard.world_bank import (
    DEFAULT_WORLD_BANK_COMPARISON_METRIC,
    WORLD_BANK_BASELINE_METRICS,
    format_world_bank_indicator,
    indicator_is_available,
    world_bank_metric,
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
