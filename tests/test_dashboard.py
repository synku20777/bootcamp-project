from __future__ import annotations

import unittest
from unittest.mock import patch

from dash.exceptions import PreventUpdate

from app.dashboard.app import (
    load_country_page,
    render_annotation_content,
    render_country_content,
    render_snowflake_status,
    retrieve_snowflake_status,
    server,
    update_annotation_page,
)


class DashboardSmokeTests(unittest.TestCase):
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
            "comment": "Reporting delay.",
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
            "Reporting delay.",
            {"state": "success", "payload": []},
        )

        post_json.assert_called_once()
        get_json.assert_not_called()
        self.assertEqual(len(state["payload"]), 1)
        rendered = render_annotation_content(state)
        self.assertIn("Annotation saved", str(rendered))


if __name__ == "__main__":
    unittest.main()
