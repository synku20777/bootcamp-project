from __future__ import annotations

from typing import Any

import dash_mantine_components as dmc
import plotly.graph_objects as go

from app.dashboard.world_bank import (
    WorldBankMetricDefinition,
    format_world_bank_indicator,
    indicator_is_available,
)

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


def world_bank_comparison_figure(
    series: list[dict[str, Any]],
    definition: WorldBankMetricDefinition,
) -> go.Figure:
    available: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    missing: list[str] = []
    for index, country in enumerate(series):
        context = country.get("world_bank_context")
        indicator = context.get(definition.key) if context else None
        if indicator_is_available(indicator):
            available.append((index, country, indicator))
        else:
            missing.append(country["country"])

    title = f"{definition.title}, {definition.year}"
    if not available:
        figure = empty_figure(f"{definition.title} is not available")
        figure.update_layout(title=title)
        return figure

    countries = [country["country"] for _, country, _ in available]
    values = [indicator["value"] for _, _, indicator in available]
    display_values = [
        format_world_bank_indicator(indicator, definition)
        for _, _, indicator in available
    ]
    figure = go.Figure(
        go.Bar(
            x=values,
            y=countries,
            orientation="h",
            marker_color=[COLORS[index % len(COLORS)] for index, _, _ in available],
            text=display_values,
            textposition="outside",
            cliponaxis=False,
            customdata=display_values,
            hovertemplate=(
                f"%{{y}}<br>{definition.title}: %{{customdata}}<extra></extra>"
            ),
        )
    )
    figure.update_layout(title=title, showlegend=False, hovermode="closest")
    xaxis: dict[str, Any] = {
        "rangemode": "tozero",
        "title_text": definition.unit,
    }
    if definition.display in {"population", "currency"}:
        xaxis["tickformat"] = ",.0f"
    elif definition.display == "density":
        xaxis["tickformat"] = ",.1f"
    else:
        xaxis["tickformat"] = ".1f"
    if definition.display == "currency":
        xaxis["tickprefix"] = "$"
    if definition.display == "percentage":
        xaxis["ticksuffix"] = "%"
    figure.update_xaxes(**xaxis)
    # The table and COVID charts use selected-country order. Retaining it here
    # keeps country colors stable instead of changing identity when metrics switch.
    figure.update_yaxes(
        autorange="reversed",
        categoryorder="array",
        categoryarray=countries,
        title_text=None,
    )
    if missing:
        figure.add_annotation(
            text="Not available: " + ", ".join(missing),
            x=0,
            y=-0.18,
            xref="paper",
            yref="paper",
            showarrow=False,
            xanchor="left",
            yanchor="top",
            font={"color": "#9aa3b2", "size": 12},
        )
    figure = apply_dashboard_chart_layout(
        figure,
        height=max(360, 54 * len(series) + 140),
    )
    figure.update_layout(
        margin={"l": 120, "r": 96, "t": 48, "b": 72 if missing else 48}
    )
    return figure


def world_bank_gdp_figure(indicators: list[dict[str, Any]]) -> go.Figure:
    available = [
        indicator
        for indicator in indicators
        if indicator.get("status") == "available" and indicator.get("value") is not None
    ]
    if not available:
        return empty_figure("Real GDP per capita is not available")

    figure = go.Figure(
        go.Scatter(
            x=[str(indicator["year"]) for indicator in available],
            y=[indicator["value"] for indicator in available],
            mode="lines+markers",
            name="Real GDP per capita",
            line={"color": COLORS[0], "width": 2},
            marker={"color": COLORS[0], "size": 8},
            connectgaps=False,
            hovertemplate=("%{x}<br>Real GDP per capita: $%{y:,.2f}<extra></extra>"),
        )
    )
    figure.update_layout(
        title="Real GDP per capita, 2019-2021",
        showlegend=False,
    )
    # A zero-inclusive scale keeps a three-point series from overstating small
    # movements. Exact values and signed changes remain available separately.
    figure.update_yaxes(
        rangemode="tozero",
        tickprefix="$",
        tickformat=",.0f",
        title_text="Constant 2015 US$",
    )
    figure.update_xaxes(type="category", title_text=None)
    return apply_dashboard_chart_layout(figure)


def forecast_figure(
    history: list[dict[str, Any]],
    forecast: list[dict[str, Any]],
    title: str,
) -> go.Figure:
    if not history or not forecast:
        return empty_figure("Not enough observations to render a forecast")

    forecast_dates = [point["report_date"] for point in forecast]
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=[point["report_date"] for point in history],
            y=[point.get("value") for point in history],
            mode="lines",
            name="Reported",
            line={"color": COLORS[0], "width": 2},
        )
    )
    figure.add_trace(
        go.Scatter(
            x=forecast_dates,
            y=[point["upper_bound"] for point in forecast],
            mode="lines",
            line={"width": 0},
            hoverinfo="skip",
            showlegend=False,
        )
    )
    figure.add_trace(
        go.Scatter(
            x=forecast_dates,
            y=[point["lower_bound"] for point in forecast],
            mode="lines",
            line={"width": 0},
            fill="tonexty",
            fillcolor="rgba(15, 138, 104, 0.18)",
            name="90% empirical interval",
            hoverinfo="skip",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=forecast_dates,
            y=[point["predicted"] for point in forecast],
            mode="lines+markers",
            name="Forecast",
            line={"color": COLORS[1], "width": 2, "dash": "dash"},
            marker={"size": 5},
        )
    )
    figure.update_layout(title=title)
    return apply_dashboard_chart_layout(figure, height=460)
