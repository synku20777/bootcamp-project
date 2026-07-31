from __future__ import annotations

from typing import Any

import dash_mantine_components as dmc
from dash import Input, Output, State, callback, html, no_update
from dash.exceptions import PreventUpdate

from app.dashboard.charts import COLORS, overview_bar, overview_map
from app.dashboard.components import create_alert, create_chart_card, create_kpi_card
from app.dashboard.layouts import status_badge
from app.dashboard.pages.common import (
    DashboardApiError,
    _error,
    _format_integer,
    _format_rate,
    _success,
    get_json,
    settings,
    triggered_id,
)


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
        and triggered_id() != "overview-retry"
    ):
        return no_update
    try:
        return _success(
            get_json(settings.dashboard_api_base_url, "/dashboard/overview")
        )
    except DashboardApiError as exc:
        return _error(exc)


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
