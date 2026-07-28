from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.config import DashboardSettings
from app.dashboard.api_client import DashboardApiError, get_json

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class DashboardConfigurationTests(unittest.TestCase):
    def test_compose_separates_internal_and_browser_visible_api_urls(self) -> None:
        compose = (REPOSITORY_ROOT / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn("DASHBOARD_API_BASE_URL: http://api:8000", compose)
        self.assertIn(
            "DASHBOARD_PUBLIC_API_BASE_URL: http://localhost:8000",
            compose,
        )

    def test_dashboard_settings_keep_internal_and_public_urls_distinct(self) -> None:
        settings = DashboardSettings(
            dashboard_api_base_url="http://api:8000",
            dashboard_public_api_base_url="http://localhost:8000",
        )

        self.assertEqual(settings.dashboard_api_base_url, "http://api:8000")
        self.assertEqual(
            settings.dashboard_public_api_base_url,
            "http://localhost:8000",
        )

    @patch("app.dashboard.api_client.requests.get")
    def test_api_error_retains_safe_code_and_request_id(self, request_get) -> None:
        response = Mock()
        response.ok = False
        response.status_code = 503
        response.headers = {"X-Request-ID": "request-456"}
        response.json.return_value = {
            "error": {
                "code": "analytics_objects_missing",
                "message": "Required analytics objects are missing.",
                "request_id": "request-456",
            }
        }
        request_get.return_value = response

        with self.assertRaises(DashboardApiError) as raised:
            get_json("http://api:8000", "/countries")

        self.assertEqual(raised.exception.code, "analytics_objects_missing")
        self.assertEqual(raised.exception.request_id, "request-456")
        self.assertNotIn("http://api:8000", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
