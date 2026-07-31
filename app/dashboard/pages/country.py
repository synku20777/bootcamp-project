from __future__ import annotations

from datetime import date
from typing import Any

import dash_mantine_components as dmc
from dash import Input, Output, callback, html

from app.dashboard.charts import COLORS, metric_figure, world_bank_gdp_figure
from app.dashboard.components import (
    create_alert,
    create_chart_card,
    create_context_metric_card,
    create_kpi_card,
)
from app.dashboard.layouts import status_badge
from app.dashboard.pages.common import (
    DashboardApiError,
    _context_change_detail,
    _context_change_value,
    _error,
    _format_integer,
    _format_rate,
    _normalized_date_range,
    _success,
    get_json,
    settings,
)
from app.dashboard.world_bank import (
    WORLD_BANK_BASELINE_METRICS,
    format_world_bank_indicator,
)


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
                f"World Development Indicators · Snapshot: {context['snapshot_id']}",
                size="xs",
                c="dimmed",
            ),
        ],
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
