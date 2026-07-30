from __future__ import annotations

from typing import Any

import dash_mantine_components as dmc
from dash import dcc
from dash_iconify import DashIconify

from app.dashboard.charts import GRAPH_CONFIG


def create_page_header(title: str, subtitle: str | None = None) -> dmc.Stack:
    """Centralizes theme and accessibility decisions for page headers."""
    children = [dmc.Title(title, order=2, size="h3")]
    if subtitle:
        children.append(dmc.Text(subtitle, c="dimmed", size="sm"))
    return dmc.Stack(children, gap="xs", mb="lg")


def create_kpi_card(title: str, value: str, icon: str | None = None) -> dmc.Paper:
    """Reusable factory for KPI cards to centralize borders, radius, and padding."""
    header = [dmc.Text(title, size="sm", c="dimmed", fw=500)]
    if icon:
        header.append(DashIconify(icon=icon, width=20, color="#687489"))

    return dmc.Paper(
        p="md",
        radius="md",
        withBorder=True,
        children=[
            dmc.Group(justify="space-between", children=header, mb="xs"),
            dmc.Text(value, size="xl", fw=700),
        ],
    )


def create_context_metric_card(
    title: str,
    value: str,
    metadata: str,
    status: str,
    icon: str,
    *,
    note: str | None = None,
) -> dmc.Paper:
    """Keep source metadata visible without competing with the metric value."""
    available = status == "available"
    children: list[Any] = [
        dmc.Group(
            justify="space-between",
            align="flex-start",
            children=[
                dmc.Text(title, size="sm", c="dimmed", fw=500),
                DashIconify(icon=icon, width=20, color="#687489"),
            ],
            mb="xs",
        ),
        dmc.Text(value, size="xl", fw=700),
        dmc.Text(metadata, size="xs", c="dimmed", mt=4),
        dmc.Badge(
            "Available" if available else "Not available",
            color="blue" if available else "gray",
            variant="light",
            size="sm",
            mt="sm",
        ),
    ]
    if note:
        children.append(dmc.Text(note, size="xs", c="dimmed", mt="sm"))
    return dmc.Paper(
        p="md",
        radius="md",
        withBorder=True,
        h="100%",
        children=children,
    )


def create_chart_card(figure: Any, *, graph_id: str | None = None) -> dmc.Paper:
    """Wraps Plotly figures in a themed Mantine card."""
    graph_options = {
        "figure": figure,
        "config": GRAPH_CONFIG,
        "responsive": True,
        "style": {"width": "100%"},
    }
    if graph_id is not None:
        graph_options["id"] = graph_id
    return dmc.Paper(
        p="md",
        radius="md",
        withBorder=True,
        miw=0,
        children=dcc.Graph(**graph_options),
    )


def create_alert(message: Any, state: str = "error") -> dmc.Alert:
    """Centralizes alert states for validation and API errors."""
    color_map = {"error": "red", "warning": "yellow", "success": "teal", "info": "blue"}
    icon_map = {
        "error": "tabler:alert-circle",
        "warning": "tabler:alert-triangle",
        "success": "tabler:check",
        "info": "tabler:info-circle",
    }
    color = color_map.get(state, "red")
    icon = icon_map.get(state, "tabler:alert-circle")

    return dmc.Alert(
        message,
        title=state.capitalize(),
        color=color,
        icon=DashIconify(icon=icon, width=20),
        variant="light",
        mb="md",
    )


def create_empty_state(message: str, icon: str = "tabler:database-off") -> dmc.Center:
    """Reusable empty state for missing data."""
    return dmc.Center(
        p="xl",
        children=dmc.Stack(
            align="center",
            gap="xs",
            children=[
                DashIconify(icon=icon, width=48, color="#687489"),
                dmc.Text(message, c="dimmed", ta="center"),
            ],
        ),
    )


def create_loading_state(
    content: Any, loading_id: str, visible: bool = False
) -> dmc.Box:
    """Wraps content with a relative positioned LoadingOverlay."""
    return dmc.Box(
        pos="relative",
        children=[
            dmc.LoadingOverlay(
                id=loading_id,
                visible=visible,
                overlayProps={"radius": "sm", "blur": 2},
                zIndex=1000,
            ),
            content,
        ],
    )


def create_form_section(title: str, children: list[Any]) -> dmc.Paper:
    """Groups form inputs neatly in a bordered card."""
    return dmc.Paper(
        p="md",
        radius="md",
        withBorder=True,
        children=dmc.Stack(
            gap="md",
            children=[dmc.Text(title, fw=600, size="sm")] + children,
        ),
    )
