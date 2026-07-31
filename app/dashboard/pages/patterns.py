from __future__ import annotations

from datetime import date
from typing import Any

import dash_mantine_components as dmc
from dash import Input, Output, callback, html

from app.dashboard.charts import case_increase_patterns_figure
from app.dashboard.components import (
    create_alert,
    create_chart_card,
    create_empty_state,
    create_kpi_card,
)
from app.dashboard.layouts import status_badge
from app.dashboard.pages.common import (
    DashboardApiError,
    _error,
    _format_integer,
    _normalized_date_range,
    _success,
    get_json,
    settings,
)


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
