from __future__ import annotations

from typing import Any

import dash_mantine_components as dmc
from dash import Input, Output, callback, html

from app.dashboard.charts import forecast_figure
from app.dashboard.components import create_alert, create_chart_card, create_kpi_card
from app.dashboard.layouts import status_badge
from app.dashboard.pages.common import (
    DashboardApiError,
    _error,
    _format_integer,
    _success,
    get_json,
    settings,
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
