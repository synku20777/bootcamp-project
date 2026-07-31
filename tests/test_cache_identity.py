from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import MagicMock

from app.config import Settings
from app.models.covid import ForecastMetric, Metric
from app.services.annotation_service import AnnotationService
from app.services.covid_service import CovidService


class CacheProbeStop(Exception):
    pass


class CacheProbe:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def get_or_compute(self, **arguments):
        self.calls.append(arguments)
        raise CacheProbeStop


class CacheIdentityTests(unittest.TestCase):
    @staticmethod
    def _capture(probe: CacheProbe, operation) -> dict[str, object]:
        with unittest.TestCase().assertRaises(CacheProbeStop):
            operation()
        return probe.calls[-1]

    def test_every_covid_cache_key_contains_selected_dataset(self) -> None:
        repository = MagicMock()
        probe = CacheProbe()
        service = CovidService(repository, probe, Settings(_env_file=None))
        start_date = date(2022, 1, 1)
        end_date = date(2022, 12, 31)
        operations = (
            service.overview,
            service.countries,
            lambda: service.case_increase_patterns(None, start_date, end_date, 3, 10),
            lambda: service.summary("LV"),
            lambda: service.timeseries("LV", Metric.NEW_CASES, start_date, end_date),
            lambda: service.compare(
                ["LV", "EE"], Metric.CASES_PER_100K, start_date, end_date
            ),
            lambda: service.country_dashboard(
                "LV", Metric.NEW_CASES, start_date, end_date
            ),
            lambda: service.dashboard_comparison(["LV", "EE"], start_date, end_date),
            lambda: service.forecast("LV", ForecastMetric.NEW_CASES, 7, 42),
        )

        for operation in operations:
            arguments = self._capture(probe, operation)
            self.assertEqual(arguments["key_payload"]["dataset"], "extended")

    def test_timeout_and_cache_lease_defaults_have_bounded_order(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.snowflake_login_timeout_seconds, 10)
        self.assertEqual(settings.snowflake_network_timeout_seconds, 30)
        self.assertEqual(settings.snowflake_statement_timeout_seconds, 30)
        self.assertEqual(settings.cache_lock_seconds, 60)
        self.assertEqual(settings.cache_lock_wait_seconds, 15)
        self.assertLess(
            settings.cache_lock_wait_seconds,
            settings.snowflake_statement_timeout_seconds,
        )
        self.assertLessEqual(
            settings.snowflake_statement_timeout_seconds,
            settings.cache_lock_seconds,
        )

    def test_annotation_validation_keys_contain_selected_dataset(self) -> None:
        probe = CacheProbe()
        service = AnnotationService(
            MagicMock(),
            MagicMock(),
            probe,
            Settings(_env_file=None, covid_dataset="legacy"),
        )

        country_arguments = self._capture(
            probe,
            lambda: service._resolve_country("LV"),
        )
        point_arguments = self._capture(
            probe,
            lambda: service._resolve_data_point("LV", date(2020, 3, 15)),
        )

        self.assertEqual(country_arguments["key_payload"]["dataset"], "legacy")
        self.assertEqual(point_arguments["key_payload"]["dataset"], "legacy")

    def test_dataset_switch_changes_cache_identity_without_namespace_change(
        self,
    ) -> None:
        extended_probe = CacheProbe()
        legacy_probe = CacheProbe()
        self._capture(
            extended_probe,
            lambda: CovidService(
                MagicMock(),
                extended_probe,
                Settings(_env_file=None, cache_namespace="shared"),
            ).summary("LV"),
        )
        self._capture(
            legacy_probe,
            lambda: CovidService(
                MagicMock(),
                legacy_probe,
                Settings(
                    _env_file=None,
                    cache_namespace="shared",
                    covid_dataset="legacy",
                ),
            ).summary("LV"),
        )

        self.assertNotEqual(
            extended_probe.calls[0]["key_payload"],
            legacy_probe.calls[0]["key_payload"],
        )


if __name__ == "__main__":
    unittest.main()
