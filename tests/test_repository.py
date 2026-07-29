from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.config import Settings
from app.models.covid import Metric
from app.repositories.snowflake_repository import SnowflakeRepository


class SnowflakeRepositoryTests(unittest.TestCase):
    @patch("app.repositories.snowflake_repository.snowflake.connector.connect")
    def test_summary_uses_bind_parameters_and_one_statement(self, connect) -> None:
        cursor = MagicMock()
        cursor.description = [
            ("COUNTRY",),
            ("COUNTRY_ISO2",),
            ("COUNTRY_ISO3",),
            ("LOCATION_KEY",),
            ("REPORT_DATE",),
            ("POPULATION",),
            ("CASES_CUMULATIVE",),
            ("DEATHS_CUMULATIVE",),
            ("CASES_PER_100K",),
            ("DEATHS_PER_100K",),
            ("MORTALITY_RATE_PERCENT",),
        ]
        cursor.fetchall.return_value = []
        connection = MagicMock()
        connection.cursor.return_value = cursor
        connect.return_value = connection

        repository = SnowflakeRepository(
            Settings(
                _env_file=None,
                snowflake_account="account",
                snowflake_user="user",
                snowflake_password="password",
            )
        )
        repository.fetch_summary("LV")

        cursor.execute.assert_called_once()
        sql, parameters = cursor.execute.call_args.args
        self.assertNotIn("LV", sql)
        self.assertEqual(parameters, ("LV", "LV", "LV", "LV"))
        cursor.close.assert_called_once()
        connection.close.assert_called_once()

    @patch("app.repositories.snowflake_repository.snowflake.connector.connect")
    def test_combined_dashboard_queries_each_execute_once(self, connect) -> None:
        cursor = MagicMock()
        cursor.description = []
        connection = MagicMock()
        connection.cursor.return_value = cursor
        connect.return_value = connection
        repository = SnowflakeRepository(
            Settings(
                _env_file=None,
                snowflake_account="account",
                snowflake_user="user",
                snowflake_password="password",
            )
        )

        repository.fetch_country_dashboard(
            "LV",
            Metric.CASES_PER_100K,
            "2020-03-01",
            "2020-12-14",
        )
        country_sql, country_parameters = cursor.execute.call_args.args
        self.assertNotIn("'LV'", country_sql)
        self.assertEqual(cursor.execute.call_count, 1)
        self.assertEqual(country_sql.count("cursor.execute"), 0)
        self.assertEqual(
            country_parameters,
            ("LV", "LV", "LV", "LV", "2020-03-01", "2020-12-14"),
        )

        cursor.reset_mock()
        repository.fetch_dashboard_comparison(
            ["LV", "EE"],
            "2020-03-01",
            "2020-12-14",
        )
        comparison_sql, comparison_parameters = cursor.execute.call_args.args
        self.assertNotIn("'LV'", comparison_sql)
        self.assertNotIn("'EE'", comparison_sql)
        self.assertEqual(
            comparison_parameters,
            ("LV", 0, "EE", 1, "2020-03-01", "2020-12-14"),
        )
        self.assertEqual(cursor.execute.call_count, 1)

    @patch("app.repositories.snowflake_repository.snowflake.connector.connect")
    def test_forecast_history_is_bounded_and_parameterized(self, connect) -> None:
        cursor = MagicMock()
        cursor.description = []
        connection = MagicMock()
        connection.cursor.return_value = cursor
        connect.return_value = connection
        repository = SnowflakeRepository(
            Settings(
                _env_file=None,
                snowflake_account="account",
                snowflake_user="user",
                snowflake_password="password",
            )
        )

        repository.fetch_forecast_history("LV", Metric.NEW_CASES, 90)

        cursor.execute.assert_called_once()
        sql, parameters = cursor.execute.call_args.args
        self.assertNotIn("'LV'", sql)
        self.assertIn("NEW_CASES_RAW", sql)
        self.assertEqual(parameters, ("LV", "LV", "LV", "LV", 90))


if __name__ == "__main__":
    unittest.main()
