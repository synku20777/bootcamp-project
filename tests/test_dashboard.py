from __future__ import annotations

import unittest
from unittest.mock import patch

from dash.exceptions import PreventUpdate

from app.dashboard.app import (
    app,
    load_comparison_page,
    load_country_page,
    notify_annotation_saved,
    render_annotation_content,
    render_country_content,
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
            annotation_page(catalog),
        ]

        self.assertEqual(len(pages), 5)
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
        )

        self.assertIn("Start date", state["message"])
        get_json.assert_not_called()

    @patch("app.dashboard.app.get_json")
    def test_country_loader_makes_one_request_and_renderer_makes_none(
        self,
        get_json,
    ) -> None:
        get_json.return_value = {
            "country": "Latvia",
            "iso2": "LV",
            "iso3": "LVA",
            "location_key": "LVA",
            "start_date": "2020-03-01",
            "end_date": "2020-12-14",
            "summary": {
                "country": "Latvia",
                "iso2": "LV",
                "iso3": "LVA",
                "location_key": "LVA",
                "report_date": "2020-12-14",
                "population": 1_900_000,
                "cases_cumulative": 25_000,
                "deaths_cumulative": 350,
                "cases_per_100k": 1315.79,
                "deaths_per_100k": 18.42,
                "mortality_rate_percent": 1.4,
            },
            "selected": {"metric": "cases_per_100k", "points": []},
            "daily_cases": {"metric": "new_cases", "points": []},
            "daily_deaths": {"metric": "new_deaths", "points": []},
            "mortality": {"metric": "mortality_rate_percent", "points": []},
        }

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
        self.assertIn("Latvia", str(rendered))
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
