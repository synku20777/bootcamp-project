from __future__ import annotations

from typing import Any

import dash_mantine_components as dmc
import plotly.graph_objects as go

dmc.add_figure_templates(default="mantine_dark")

COLORS = ["#315fd4", "#0f8a68", "#d26a3f", "#805ad5", "#c13f66"]

GRAPH_CONFIG = {
    "displaylogo": False,
    "responsive": True,
    "scrollZoom": False,
    "modeBarButtonsToRemove": [
        "lasso2d",
        "select2d",
    ],
}


def empty_figure(message: str) -> go.Figure:
    figure = go.Figure()
    figure.add_annotation(
        text=message,
        x=0.5,
        y=0.5,
        xref="paper",
        yref="paper",
        showarrow=False,
        font={"color": "#687489", "size": 14},
    )
    figure.update_xaxes(visible=False)
    figure.update_yaxes(visible=False)
    return apply_dashboard_chart_layout(figure)


def apply_dashboard_chart_layout(figure: go.Figure, *, height: int = 380) -> go.Figure:
    return figure.update_layout(
        height=height,
        margin={"l": 24, "r": 16, "t": 48, "b": 32},
        font={"family": "Poppins, Inter, system-ui, sans-serif"},
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.08, "x": 0},
    )


def overview_bar(
    locations: list[dict[str, Any]],
    value_key: str,
    title: str,
    color: str,
) -> go.Figure:
    ranked = sorted(
        locations,
        key=lambda row: row.get(value_key) or 0,
        reverse=True,
    )[:10]
    ranked.reverse()
    figure = go.Figure(
        go.Bar(
            x=[row.get(value_key) for row in ranked],
            y=[row["country"] for row in ranked],
            orientation="h",
            marker_color=color,
            hovertemplate="%{y}: %{x:,.0f}<extra></extra>",
        )
    )
    figure.update_layout(title=title)
    return apply_dashboard_chart_layout(figure)


def overview_map(locations: list[dict[str, Any]]) -> go.Figure:
    mapped = [row for row in locations if row.get("iso3")]
    figure = go.Figure(
        go.Choropleth(
            locations=[row["iso3"] for row in mapped],
            z=[row.get("cases_per_100k") for row in mapped],
            text=[row["country"] for row in mapped],
            colorscale="Blues",
            marker_line_color="#ffffff",
            marker_line_width=0.4,
            colorbar_title="Cases / 100k",
            hovertemplate="%{text}<br>%{z:,.1f} cases / 100k<extra></extra>",
        )
    )
    figure.update_layout(
        title="Cases per 100,000",
        geo={
            "showframe": False,
            "showcoastlines": False,
            "projection_type": "natural earth",
        },
    )
    return apply_dashboard_chart_layout(figure)


def metric_figure(
    points: list[dict[str, Any]],
    title: str,
    *,
    color: str,
    chart_type: str = "line",
) -> go.Figure:
    if not points:
        return empty_figure("No observations in this date range")
    trace_type = go.Bar if chart_type == "bar" else go.Scatter
    trace_options: dict[str, Any] = {
        "x": [point["report_date"] for point in points],
        "y": [point.get("value") for point in points],
        "name": title,
    }
    if chart_type == "bar":
        trace_options["marker_color"] = color
    else:
        trace_options.update({"mode": "lines", "line": {"color": color, "width": 2}})
    figure = go.Figure(trace_type(**trace_options))
    figure.update_layout(title=title, showlegend=False)
    return apply_dashboard_chart_layout(figure)


def comparison_figure(
    series: list[dict[str, Any]],
    series_key: str,
    title: str,
) -> go.Figure:
    figure = go.Figure()
    for index, country in enumerate(series):
        points = country[series_key]["points"]
        figure.add_trace(
            go.Scatter(
                x=[point["report_date"] for point in points],
                y=[point.get("value") for point in points],
                mode="lines",
                name=country["country"],
                line={"color": COLORS[index % len(COLORS)], "width": 2},
            )
        )
    if not any(country[series_key]["points"] for country in series):
        return empty_figure("No observations in this date range")
    figure.update_layout(title=title)
    return apply_dashboard_chart_layout(figure)
