from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from app.config import Settings
from app.models.covid import Metric
from app.repositories.snowflake_repository import SnowflakeRepository


class SnowflakeRepositoryTests(unittest.TestCase):
    def test_dataset_setting_rejects_unknown_object_selection(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, covid_dataset="arbitrary_table")

    @patch("app.repositories.snowflake_repository.snowflake.connector.connect")
    def test_dataset_selection_uses_allowlisted_extended_and_legacy_objects(
        self,
        connect,
    ) -> None:
        cursor = MagicMock()
        cursor.description = []
        cursor.fetchall.return_value = []
        connection = MagicMock()
        connection.cursor.return_value = cursor
        connect.return_value = connection

        extended = SnowflakeRepository(
            Settings(
                _env_file=None,
                snowflake_account="account",
                snowflake_user="user",
                snowflake_password="password",
            )
        )
        extended.fetch_summary("LV")
        extended_sql = " ".join(cursor.execute.call_args.args[0].split())
        self.assertIn("COUNTRY_LATEST_METRICS_EXTENDED", extended_sql)
        self.assertIn(
            "COALESCE(DENOMINATOR_PUBLICATION_STATUS, 'ACTIVE') "
            "AS DENOMINATOR_PUBLICATION_STATUS",
            extended_sql,
        )

        cursor.reset_mock()
        legacy = SnowflakeRepository(
            Settings(
                _env_file=None,
                snowflake_account="account",
                snowflake_user="user",
                snowflake_password="password",
                covid_dataset="legacy",
            )
        )
        legacy.fetch_summary("LV")
        legacy_sql = " ".join(cursor.execute.call_args.args[0].split())
        self.assertIn("MARTS.COUNTRY_LATEST_METRICS ", legacy_sql)
        self.assertNotIn("COUNTRY_LATEST_METRICS_EXTENDED", legacy_sql)
        self.assertIn("'ACTIVE' AS DENOMINATOR_PUBLICATION_STATUS", legacy_sql)

    @patch("app.repositories.snowflake_repository.snowflake.connector.connect")
    def test_context_uses_selected_latest_date_and_optional_wdi_join(
        self,
        connect,
    ) -> None:
        cursor = MagicMock()
        cursor.description = []
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

        repository.fetch_country_context("FSM")

        sql, parameters = cursor.execute.call_args.args
        self.assertIn("COUNTRY_LATEST_METRICS_EXTENDED", sql)
        self.assertIn("resolved.REPORT_DATE AS COVID_LATEST_REPORT_DATE", sql)
        self.assertIn("LEFT JOIN COVID_ANALYTICS.MARTS.COUNTRY_CONTEXT_ANALYSIS", sql)
        self.assertEqual(parameters, ("FSM", "FSM", "FSM", "FSM"))

    @patch("app.repositories.snowflake_repository.snowflake.connector.connect")
    def test_patterns_use_selected_object_binds_and_exact_prelimit_summary(
        self,
        connect,
    ) -> None:
        cursor = MagicMock()
        cursor.description = []
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

        repository.fetch_case_increase_patterns(
            "FSM",
            date(2022, 1, 1),
            date(2023, 3, 9),
            5,
            25,
        )

        sql, parameters = cursor.execute.call_args.args
        normalized = " ".join(sql.split())
        self.assertIn("CASE_INCREASE_PATTERNS_EXTENDED", normalized)
        self.assertIn("END_DATE >= %s", normalized)
        self.assertIn("START_DATE <= %s", normalized)
        self.assertIn("COUNT(*) AS TOTAL_PATTERNS", normalized)
        self.assertLess(normalized.index("SUMMARY AS"), normalized.index("RANKED AS"))
        self.assertEqual(
            parameters,
            (
                date(2022, 1, 1),
                date(2023, 3, 9),
                5,
                "FSM",
                "FSM",
                "FSM",
                "FSM",
                25,
            ),
        )

        cursor.reset_mock()
        legacy = SnowflakeRepository(
            Settings(
                _env_file=None,
                snowflake_account="account",
                snowflake_user="user",
                snowflake_password="password",
                covid_dataset="legacy",
            )
        )
        legacy.fetch_case_increase_patterns(
            None,
            date(2020, 3, 1),
            date(2020, 12, 14),
            3,
            100,
        )
        legacy_sql = " ".join(cursor.execute.call_args.args[0].split())
        self.assertIn("MARTS.CASE_INCREASE_PATTERNS ", legacy_sql)
        self.assertNotIn("CASE_INCREASE_PATTERNS_EXTENDED", legacy_sql)

    @patch("app.repositories.snowflake_repository.snowflake.connector.connect")
    def test_summary_uses_bind_parameters_and_one_statement(self, connect) -> None:
        cursor = MagicMock()
        cursor.description = [
            ("COUNTRY",),
            ("COUNTRY_ISO2",),
            ("COUNTRY_ISO3",),
            ("LOCATION_KEY",),
            ("REPORT_DATE",),
            ("COVID_RATE_POPULATION_2020",),
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
        self.assertIn("COUNTRY_CONTEXT_ANALYSIS", comparison_sql)
        self.assertIn("resolved.COUNTRY_ISO3 = context.ISO3", comparison_sql)
        for required_column in (
            "POPULATION_2020_CONTEXT",
            "POPULATION_DENSITY_2019",
            "POPULATION_AGE_65_PLUS_PCT_2019",
            "REAL_GDP_PER_CAPITA_2019",
            "HEALTH_EXPENDITURE_PER_CAPITA_PPP_2019",
            "CONTEXT_SNAPSHOT_ID",
        ):
            self.assertIn(required_column, comparison_sql)

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
