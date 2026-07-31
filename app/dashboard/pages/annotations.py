from __future__ import annotations

from datetime import date
from typing import Any

import dash_mantine_components as dmc
from dash import Input, Output, State, callback, html
from dash.exceptions import PreventUpdate

from app.dashboard.components import create_alert
from app.dashboard.layouts import status_badge
from app.dashboard.pages.common import (
    DashboardApiError,
    _error,
    _normalized_date_range,
    _success,
    get_json,
    post_json,
    settings,
    triggered_id,
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
        if triggered_id() != "annotation-submit":
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
