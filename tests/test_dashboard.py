from __future__ import annotations

import unittest
from copy import deepcopy
from unittest.mock import patch

from dash.exceptions import PreventUpdate

from app.dashboard.app import (
    app,
    load_comparison_page,
    load_country_catalog,
    load_country_page,
    load_forecast_page,
    notify_annotation_saved,
    render_annotation_content,
    render_comparison_content,
    render_country_content,
    render_forecast_content,
    render_snowflake_status,
    retrieve_snowflake_status,
    server,
    update_annotation_page,
    update_app_shell_navbar,
)
from app.dashboard.layouts import (
    annotation_page,
    comparison_page,
    country_page,
    forecast_page,
    overview_page,
    status_page,
)


def walk_components(component):
    if component is None:
        return
    if isinstance(component, (list, tuple)):
        for child in component:
            yield from walk_components(child)
        return
    yield component
    yield from walk_components(getattr(component, "children", None))


def component_by_id(component, component_id: str):
    return next(
        item
        for item in walk_components(component)
        if getattr(item, "id", None) == component_id
    )


WORLD_BANK_SNAPSHOT_ID = "wdi2-2019-2021-372906f371e0391f"


def context_indicator(
    value: float | int | None,
    year: int,
    unit: str,
    indicator_code: str,
    *,
    status: str | None = None,
) -> dict[str, object]:
    return {
        "value": value,
        "status": status or ("available" if value is not None else "missing"),
        "year": year,
        "unit": unit,
        "indicator_code": indicator_code,
        "snapshot_id": WORLD_BANK_SNAPSHOT_ID,
    }


def context_change(
    value: float | None,
    baseline_year: int,
    comparison_year: int,
    *,
    status: str = "available",
) -> dict[str, object]:
    return {
        "value": value,
        "status": status,
        "baseline_year": baseline_year,
        "comparison_year": comparison_year,
        "unit": "percent",
    }


def latvia_context() -> dict[str, object]:
    annual_gdp = [
        context_indicator(value, year, "constant 2015 US$", "NY.GDP.PCAP.KD")
        for year, value in (
            (2019, 15328.38599333),
            (2020, 14900.729571887),
            (2021, 16070.168003232),
        )
    ]
    return {
        "country": "Latvia",
        "iso2": "LV",
        "iso3": "LVA",
        "location_key": "LVA",
        "population_2020_context": context_indicator(
            1900449,
            2020,
            "people",
            "SP.POP.TOTL",
        ),
        "covid_rate_population_2020": context_indicator(
            1900000,
            2020,
            "people",
            "SP.POP.TOTL",
        ),
        "population_density_2019": context_indicator(
            30.755491989,
            2019,
            "people per sq. km of land area",
            "EN.POP.DNST",
        ),
        "population_age_65_plus_pct_2019": context_indicator(
            20.40722722,
            2019,
            "% of total population",
            "SP.POP.65UP.TO.ZS",
        ),
        "real_gdp_per_capita_2019": annual_gdp[0],
        "health_expenditure_per_capita_ppp_2019": context_indicator(
            2202.675929217,
            2019,
            "current international $",
            "SH.XPD.CHEX.PP.CD",
        ),
        "real_gdp_per_capita_annual": annual_gdp,
        "real_gdp_per_capita_change_2020_vs_2019": context_change(
            -2.78996380720768,
            2019,
            2020,
        ),
        "real_gdp_per_capita_change_2021_vs_2019": context_change(
            4.83927016337389,
            2019,
            2021,
        ),
        "real_gdp_per_capita_change_2021_vs_2020": context_change(
            7.84819579271718,
            2020,
            2021,
        ),
        "covid_latest_report_date": "2020-12-14",
        "snapshot_id": WORLD_BANK_SNAPSHOT_ID,
        "methodology": {
            "classification": "descriptive",
            "caveat": "Changes during the pandemic period do not establish causality.",
        },
    }


def country_dashboard_payload(
    context: dict[str, object] | None,
    *,
    country: str = "Latvia",
    iso2: str = "LV",
    iso3: str = "LVA",
) -> dict[str, object]:
    point = [{"report_date": "2020-12-14", "value": 1.0}]
    return {
        "country": country,
        "iso2": iso2,
        "iso3": iso3,
        "location_key": iso3,
        "start_date": "2020-03-01",
        "end_date": "2020-12-14",
        "summary": {
            "country": country,
            "iso2": iso2,
            "iso3": iso3,
            "location_key": iso3,
            "report_date": "2020-12-14",
            "covid_rate_population_2020": 1900000,
            "cases_cumulative": 25000,
            "deaths_cumulative": 350,
            "cases_per_100k": 1315.79,
            "deaths_per_100k": 18.42,
            "mortality_rate_percent": 1.4,
        },
        "selected": {"metric": "cases_per_100k", "points": point},
        "daily_cases": {"metric": "new_cases", "points": point},
        "daily_deaths": {"metric": "new_deaths", "points": point},
        "mortality": {"metric": "mortality_rate_percent", "points": point},
        "context": context,
        "context_status": "available" if context else "context_data_unavailable",
    }


class DashboardSmokeTests(unittest.TestCase):
    @staticmethod
    def _catalog_state() -> dict[str, object]:
        return {
            "state": "success",
            "payload": [
                {
                    "country": "Latvia",
                    "iso2": "LV",
                    "iso3": "LVA",
                    "location_key": "ISO2:LV",
                },
                {
                    "country": "Estonia",
                    "iso2": "EE",
                    "iso3": "EST",
                    "location_key": "ISO2:EE",
                },
            ],
        }

    def test_status_page_loads_without_calling_snowflake(self) -> None:
        client = server.test_client()
        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"COVID-19 Analytics Platform", response.data)

        layout = client.get("/_dash-layout")
        self.assertEqual(layout.status_code, 200)
        self.assertIn(b'"country-page-data"', layout.data)
        self.assertIn(b'"storage_type":"memory"', layout.data)
        self.assertIn(b'"country-catalog"', layout.data)
        self.assertIn(b'"storage_type":"session"', layout.data)

    def test_snowflake_status_starts_not_checked(self) -> None:
        component = render_snowflake_status(None)

        self.assertIn("Not checked", str(component))
        self.assertIn("No warehouse query made", str(component))

    @patch("app.dashboard.app.get_json")
    def test_snowflake_callback_requires_a_real_click(self, get_json) -> None:
        with self.assertRaises(PreventUpdate):
            retrieve_snowflake_status(0)

        get_json.assert_not_called()

    def test_every_migrated_route_constructs_with_dmc_2_8(self) -> None:
        catalog = self._catalog_state()

        pages = [
            status_page("http://localhost:8000"),
            overview_page(),
            country_page(catalog),
            comparison_page(catalog),
            forecast_page(catalog),
            annotation_page(catalog),
        ]

        self.assertEqual(len(pages), 6)
        self.assertEqual(app.layout.__class__.__name__, "MantineProvider")

    def test_dmc_component_properties_match_runtime_contracts(self) -> None:
        catalog = self._catalog_state()
        mobile_toggle = component_by_id(app.layout, "mobile-sidebar-toggle")
        comparison_select = component_by_id(
            comparison_page(catalog),
            "comparison-countries",
        )
        annotation_name = component_by_id(
            annotation_page(catalog),
            "annotation-created-by",
        )
        annotation_comment = component_by_id(
            annotation_page(catalog),
            "annotation-comment",
        )

        self.assertEqual(mobile_toggle.__class__.__name__, "ActionIcon")
        self.assertIn("n_clicks", mobile_toggle._prop_names)
        self.assertEqual(comparison_select.maxValues, 10)
        self.assertEqual(annotation_name.inputProps["maxLength"], 80)
        self.assertEqual(annotation_comment.inputProps["maxLength"], 1000)

    def test_sidebar_state_keeps_mobile_width_independent(self) -> None:
        navbar, class_name = update_app_shell_navbar(
            {"compact": True},
            {"open": True},
        )

        self.assertEqual(navbar["width"], {"base": 280, "sm": 72})
        self.assertFalse(navbar["collapsed"]["mobile"])
        self.assertIn("app-navbar--compact", class_name)

    @patch("app.dashboard.app.get_json")
    def test_invalid_country_date_ranges_do_not_call_api(self, get_json) -> None:
        reversed_range = load_country_page(
            "LV",
            "cases_per_100k",
            "2020-12-14",
            "2020-03-01",
            0,
        )
        out_of_bounds = load_country_page(
            "LV",
            "cases_per_100k",
            "2019-12-31",
            "2020-12-14",
            0,
        )
        missing_end = load_country_page(
            "LV",
            "cases_per_100k",
            "2020-03-01",
            None,
            0,
        )

        self.assertIn("Start date", reversed_range["message"])
        self.assertIn("available range", out_of_bounds["message"])
        self.assertIn("complete date range", missing_end["message"])
        get_json.assert_not_called()

    @patch("app.dashboard.app.get_json")
    def test_invalid_comparison_range_does_not_call_api(self, get_json) -> None:
        state = load_comparison_page(
            ["LV", "EE"],
            "2020-12-14",
            "2020-03-01",
            0,
            self._catalog_state(),
        )

        self.assertIn("Start date", state["message"])
        get_json.assert_not_called()

    @patch("app.dashboard.app.get_json")
    def test_comparison_catalog_failure_suppresses_selection_validation(
        self,
        get_json,
    ) -> None:
        catalog_error = {
            "state": "error",
            "message": "Snowflake is temporarily unavailable.",
            "code": "snowflake_unavailable",
            "request_id": "request-123",
        }
        page = comparison_page(catalog_error)
        selector = component_by_id(page, "comparison-countries")
        comparison_retry = component_by_id(page, "comparison-retry")

        state = load_comparison_page(
            [],
            "2020-03-01",
            "2020-12-14",
            0,
            catalog_error,
        )
        rendered = render_comparison_content(state)

        self.assertTrue(selector.disabled)
        self.assertTrue(comparison_retry.disabled)
        self.assertEqual(state["state"], "upstream_error")
        self.assertNotIn("Choose between 2 and 10", str(page))
        self.assertNotIn("Choose between 2 and 10", str(rendered))
        self.assertIn("request-123", str(page))
        get_json.assert_not_called()

    @patch("app.dashboard.app.get_json")
    def test_empty_initial_comparison_selection_is_neutral(self, get_json) -> None:
        state = load_comparison_page(
            [],
            "2020-03-01",
            "2020-12-14",
            0,
            self._catalog_state(),
        )

        self.assertEqual(state, {"state": "neutral"})
        self.assertNotIn(
            "Choose between 2 and 10", str(render_comparison_content(state))
        )
        get_json.assert_not_called()

    @patch("app.dashboard.app.ctx")
    @patch("app.dashboard.app.get_json")
    def test_catalog_retry_recovers_after_api_failure(
        self,
        get_json,
        callback_context,
    ) -> None:
        callback_context.triggered_id = "retry-catalog"
        get_json.return_value = self._catalog_state()["payload"]

        state = load_country_catalog(
            "/compare",
            1,
            {"state": "error", "message": "Unavailable"},
        )

        self.assertEqual(state["state"], "success")
        self.assertEqual(len(state["payload"]), 2)
        get_json.assert_called_once()

    @patch("app.dashboard.app.get_json")
    def test_country_loader_makes_one_request_and_renderer_makes_none(
        self,
        get_json,
    ) -> None:
        get_json.return_value = country_dashboard_payload(None)

        state = load_country_page(
            "LV",
            "cases_per_100k",
            "2020-03-01",
            "2020-12-14",
            0,
        )
        self.assertEqual(get_json.call_count, 1)

        get_json.reset_mock()
        rendered = render_country_content(state)
        rendered_text = str(rendered)
        self.assertIn("Latvia", rendered_text)
        self.assertIn("Cases", rendered_text)
        self.assertIn(
            "World Bank context is unavailable for this country or active snapshot.",
            rendered_text,
        )
        get_json.assert_not_called()

    def test_country_renderer_displays_complete_latvia_context(self) -> None:
        rendered = render_country_content(
            {"state": "success", "payload": country_dashboard_payload(latvia_context())}
        )
        rendered_text = str(rendered)

        for expected in (
            "1,900,449",
            "30.8",
            "20.4%",
            "$15,328",
            "$2,203",
            "SP.POP.TOTL",
            "EN.POP.DNST",
            "SP.POP.65UP.TO.ZS",
            "NY.GDP.PCAP.KD",
            "SH.XPD.CHEX.PP.CD",
            "-2.79%",
            "+4.84%",
            "+7.85%",
            "Changes during the pandemic period do not establish causality.",
        ):
            self.assertIn(expected, rendered_text)

        footer = "World Development Indicators · Snapshot: " f"{WORLD_BANK_SNAPSHOT_ID}"
        self.assertEqual(rendered_text.count(footer), 1)

        components = list(walk_components(rendered))
        covid_card_index = next(
            index
            for index, component in enumerate(components)
            if getattr(component, "children", None) == "COVID rate denominator, 2020"
        )
        context_index = next(
            index
            for index, component in enumerate(components)
            if getattr(component, "children", None) == "World Bank country context"
        )
        daily_cases_index = next(
            index
            for index, component in enumerate(components)
            if component.__class__.__name__ == "Graph"
            and component.figure.layout.title.text == "Daily cases"
        )
        gdp_graph_index = next(
            index
            for index, component in enumerate(components)
            if component.__class__.__name__ == "Graph"
            and component.figure.layout.title.text == "Real GDP per capita, 2019-2021"
        )
        self.assertLess(covid_card_index, context_index)
        self.assertLess(context_index, daily_cases_index)
        self.assertLess(daily_cases_index, gdp_graph_index)

        gdp_graph = components[gdp_graph_index]
        self.assertEqual(gdp_graph.figure.layout.yaxis.rangemode, "tozero")
        self.assertEqual(gdp_graph.figure.layout.yaxis.tickformat, ",.0f")
        self.assertIn("$%{y:,.2f}", gdp_graph.figure.data[0].hovertemplate)
        self.assertEqual(
            list(gdp_graph.figure.data[0].y),
            [15328.38599333, 14900.729571887, 16070.168003232],
        )

    def test_country_renderer_handles_committed_missing_data_patterns(self) -> None:
        aruba = latvia_context()
        aruba["health_expenditure_per_capita_ppp_2019"] = context_indicator(
            None,
            2019,
            "current international $",
            "SH.XPD.CHEX.PP.CD",
        )

        eritrea = latvia_context()
        eritrea["real_gdp_per_capita_2019"] = context_indicator(
            None,
            2019,
            "constant 2015 US$",
            "NY.GDP.PCAP.KD",
        )
        eritrea["real_gdp_per_capita_annual"] = [
            context_indicator(
                None,
                year,
                "constant 2015 US$",
                "NY.GDP.PCAP.KD",
            )
            for year in (2019, 2020, 2021)
        ]
        for key, baseline_year, comparison_year in (
            ("real_gdp_per_capita_change_2020_vs_2019", 2019, 2020),
            ("real_gdp_per_capita_change_2021_vs_2019", 2019, 2021),
            ("real_gdp_per_capita_change_2021_vs_2020", 2020, 2021),
        ):
            eritrea[key] = context_change(
                None,
                baseline_year,
                comparison_year,
                status="missing_input",
            )

        kosovo = latvia_context()
        kosovo["population_density_2019"] = context_indicator(
            None,
            2019,
            "people per sq. km of land area",
            "EN.POP.DNST",
        )
        kosovo["health_expenditure_per_capita_ppp_2019"] = context_indicator(
            None,
            2019,
            "current international $",
            "SH.XPD.CHEX.PP.CD",
        )

        isolated_density = deepcopy(latvia_context())
        isolated_density["population_density_2019"] = context_indicator(
            None,
            2019,
            "people per sq. km of land area",
            "EN.POP.DNST",
        )

        cases = (
            ("Aruba", aruba, 2),
            ("Kosovo", kosovo, 4),
            ("Synthetic isolated density", isolated_density, 2),
        )
        for name, context, expected_unavailable_count in cases:
            with self.subTest(name=name):
                rendered = render_country_content(
                    {
                        "state": "success",
                        "payload": country_dashboard_payload(context),
                    }
                )
                rendered_text = str(rendered)
                self.assertEqual(
                    rendered_text.count("Not available"),
                    expected_unavailable_count,
                )
                self.assertNotIn("World Bank context is unavailable", rendered_text)

        eritrea_rendered = str(
            render_country_content(
                {
                    "state": "success",
                    "payload": country_dashboard_payload(eritrea),
                }
            )
        )
        self.assertEqual(eritrea_rendered.count("Not available"), 5)
        self.assertIn("Real GDP per capita is not available", eritrea_rendered)
        self.assertIn("A required annual GDP value is missing.", eritrea_rendered)

    def test_country_renderer_explains_zero_gdp_denominator(self) -> None:
        context = latvia_context()
        context["real_gdp_per_capita_change_2020_vs_2019"] = context_change(
            None,
            2019,
            2020,
            status="zero_denominator",
        )

        rendered = render_country_content(
            {"state": "success", "payload": country_dashboard_payload(context)}
        )

        self.assertIn("The baseline GDP value is zero.", str(rendered))

    @patch("app.dashboard.app.get_json")
    def test_forecast_loader_makes_one_request_and_renders_evaluation(
        self,
        get_json,
    ) -> None:
        get_json.return_value = {
            "country": "Latvia",
            "iso2": "LV",
            "iso3": "LVA",
            "location_key": "LVA",
            "metric": "new_cases",
            "historical_start_date": "2020-09-16",
            "historical_end_date": "2020-12-14",
            "horizon_days": 30,
            "lookback_days": 90,
            "training_observations": 90,
            "interval_level_percent": 90,
            "history": [{"report_date": "2020-12-14", "value": 500}],
            "forecast": [
                {
                    "report_date": "2020-12-15",
                    "predicted": 510,
                    "lower_bound": 450,
                    "upper_bound": 570,
                }
            ],
            "evaluation": {
                "holdout_start_date": "2020-12-01",
                "holdout_observations": 14,
                "moving_average_mae": 80,
                "moving_average_rmse": 100,
                "linear_trend_mae": 70,
                "linear_trend_rmse": 90,
                "selected_model": "linear_trend",
                "selected_mae": 70,
                "selected_rmse": 90,
            },
            "caveats": ["Historical demonstration only."],
        }

        state = load_forecast_page("LV", "new_cases", "30", "90", 0)

        self.assertEqual(get_json.call_count, 1)
        get_json.reset_mock()
        rendered = render_forecast_content(state)
        self.assertIn("Linear Trend", str(rendered))
        self.assertIn("Temporal validation", str(rendered))
        self.assertNotIn("World Bank country context", str(rendered))
        self.assertNotIn("Pre-pandemic country context", str(rendered))
        get_json.assert_not_called()

    @patch("app.dashboard.app.ctx")
    @patch("app.dashboard.app.post_json")
    @patch("app.dashboard.app.get_json")
    def test_annotation_submit_appends_without_a_followup_get(
        self,
        get_json,
        post_json,
        callback_context,
    ) -> None:
        callback_context.triggered_id = "annotation-submit"
        post_json.return_value = {
            "id": "507f1f77bcf86cd799439011",
            "country": "Latvia",
            "iso2": "LV",
            "iso3": "LVA",
            "location_key": "LVA",
            "report_date": "2020-03-15",
            "metric": "new_cases",
            "comment": "Reporting delay.\nConfirmed by source.",
            "created_by": "Student",
            "created_at": "2026-07-26T09:00:00Z",
        }

        state = update_annotation_page(
            "LV",
            "",
            "2020-03-01",
            "2020-12-14",
            0,
            1,
            "2020-03-15",
            "new_cases",
            "Student",
            "Reporting delay.\nConfirmed by source.",
            {"state": "success", "payload": []},
        )

        post_json.assert_called_once()
        get_json.assert_not_called()
        self.assertEqual(len(state["payload"]), 1)
        self.assertEqual(
            state["payload"][0]["comment"],
            "Reporting delay.\nConfirmed by source.",
        )
        rendered = render_annotation_content(state)
        self.assertIn("whiteSpace", str(rendered))
        self.assertIn("pre-wrap", str(rendered))

        cleared_comment, notifications = notify_annotation_saved(state)
        self.assertEqual(cleared_comment, "")
        self.assertEqual(notifications[0]["action"], "show")
        self.assertEqual(notifications[0]["title"], "Annotation saved")


if __name__ == "__main__":
    unittest.main()
