from __future__ import annotations

import inspect
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from app.config import Settings
from app.exceptions import DataSourceUnavailableError
from app.repositories.snowflake_repository import SnowflakeRepository
from app.services.cache_service import CacheStatus
from app.services.covid_service import CovidService
from app.world_bank_manifest import canonical_context_iso3

SNAPSHOT_ID = "wdi2-2019-2021-test"


def comparison_row(**overrides):
    row = {
        "REQUEST_ORDER": 0,
        "COUNTRY": "Latvia",
        "COUNTRY_ISO2": "LV",
        "COUNTRY_ISO3": "LVA",
        "LOCATION_KEY": "LVA",
        "REPORT_DATE": date(2020, 12, 14),
        "CASES_PER_100K": 1315.79,
        "DEATHS_PER_100K": 18.42,
        "MORTALITY_RATE_PERCENT": 1.4,
        "POPULATION_2020_CONTEXT": 1_900_449,
        "POPULATION_2020_STATUS": "available",
        "POPULATION_DENSITY_2019": 30.755491989,
        "POPULATION_DENSITY_2019_STATUS": "available",
        "POPULATION_AGE_65_PLUS_PCT_2019": 20.40722722,
        "AGE_65_PLUS_2019_STATUS": "available",
        "REAL_GDP_PER_CAPITA_2019": 15_328.38599333,
        "REAL_GDP_PER_CAPITA_2019_STATUS": "available",
        "HEALTH_EXPENDITURE_PER_CAPITA_PPP_2019": 2_202.675929217,
        "HEALTH_EXPENDITURE_PPP_2019_STATUS": "available",
        "CONTEXT_SNAPSHOT_ID": SNAPSHOT_ID,
    }
    row.update(overrides)
    return row


class RecordingCache:
    def __init__(self) -> None:
        self.arguments = None

    def get_or_compute(self, **arguments):
        self.arguments = arguments
        return "cached-comparison", CacheStatus.HIT


class WorldBankContextTests(unittest.TestCase):
    def test_context_cache_identity_canonicalizes_to_iso3_offline(self) -> None:
        manifest = "data/external/world_bank_indicators_2019_2021.manifest.json"
        self.assertEqual(canonical_context_iso3("LV", manifest), "LVA")
        self.assertEqual(canonical_context_iso3("Latvia", manifest), "LVA")

    def test_manifest_mismatch_is_context_specific(self) -> None:
        row = {
            "CONTEXT_SNAPSHOT_ID": "database-snapshot",
            "COUNTRY": "Latvia",
            "COUNTRY_ISO2": "LV",
            "COUNTRY_ISO3": "LVA",
            "LOCATION_KEY": "LVA",
            "COVID_LATEST_REPORT_DATE": date(2020, 12, 14),
        }
        with self.assertRaisesRegex(
            DataSourceUnavailableError,
            "Country context data is temporarily unavailable",
        ):
            CovidService._country_context(row, "manifest-snapshot")

    def test_comparison_baseline_context_preserves_committed_missing_patterns(
        self,
    ) -> None:
        patterns = {
            "Latvia": ({}, set()),
            "Aruba": (
                {
                    "HEALTH_EXPENDITURE_PER_CAPITA_PPP_2019": None,
                    "HEALTH_EXPENDITURE_PPP_2019_STATUS": "missing",
                },
                {"health_expenditure_per_capita_ppp_2019"},
            ),
            "Eritrea": (
                {
                    "REAL_GDP_PER_CAPITA_2019": None,
                    "REAL_GDP_PER_CAPITA_2019_STATUS": "missing",
                },
                {"real_gdp_per_capita_2019"},
            ),
            "Kosovo": (
                {
                    "POPULATION_DENSITY_2019": None,
                    "POPULATION_DENSITY_2019_STATUS": "missing",
                    "HEALTH_EXPENDITURE_PER_CAPITA_PPP_2019": None,
                    "HEALTH_EXPENDITURE_PPP_2019_STATUS": "missing",
                },
                {
                    "population_density_2019",
                    "health_expenditure_per_capita_ppp_2019",
                },
            ),
            "Synthetic isolated density": (
                {
                    "POPULATION_DENSITY_2019": None,
                    "POPULATION_DENSITY_2019_STATUS": "missing",
                },
                {"population_density_2019"},
            ),
        }

        for country, (overrides, expected_missing) in patterns.items():
            with self.subTest(country=country):
                context = CovidService._world_bank_baseline_context(
                    comparison_row(**overrides),
                    SNAPSHOT_ID,
                )
                actual_missing = {
                    field
                    for field, indicator in context.model_dump().items()
                    if field != "snapshot_id" and indicator["status"] != "available"
                }
                self.assertEqual(actual_missing, expected_missing)

    def test_absent_or_mismatched_context_does_not_suppress_covid(self) -> None:
        for active_snapshot in (None, "stale-publication"):
            with self.subTest(active_snapshot=active_snapshot):
                rows = [comparison_row(CONTEXT_SNAPSHOT_ID=active_snapshot)]

                series, missing_country = CovidService._dashboard_comparison_series(
                    rows,
                    set(),
                    SNAPSHOT_ID,
                )

                self.assertIsNone(series.world_bank_context)
                self.assertEqual(
                    series.world_bank_context_status,
                    "context_data_unavailable",
                )
                self.assertEqual(series.cases_per_100k.points[0].value, 1315.79)
                self.assertIsNone(missing_country)

    @patch(
        "app.services.covid_service.committed_snapshot_id",
        return_value=SNAPSHOT_ID,
    )
    def test_comparison_cache_identity_includes_contract_and_snapshot(
        self,
        _snapshot_id,
    ) -> None:
        cache = RecordingCache()
        repository = MagicMock()
        service = CovidService(
            repository,
            cache,
            Settings(_env_file=None),
        )

        result, cache_status = service.dashboard_comparison(
            ["LV", "EE"],
            date(2020, 3, 1),
            date(2020, 12, 14),
        )

        self.assertEqual(result, "cached-comparison")
        self.assertEqual(cache_status, CacheStatus.HIT)
        self.assertEqual(cache.arguments["endpoint"], "comparison-page")
        self.assertEqual(cache.arguments["key_payload"]["version"], 2)
        self.assertEqual(
            cache.arguments["key_payload"]["context_snapshot_id"],
            SNAPSHOT_ID,
        )
        repository.fetch_dashboard_comparison.assert_not_called()

    def test_forecasting_repository_and_cache_are_context_isolated(self) -> None:
        repository_source = inspect.getsource(
            SnowflakeRepository.fetch_forecast_history
        )
        service_source = inspect.getsource(CovidService.forecast)
        for forbidden in (
            "WORLD_BANK",
            "COUNTRY_CONTEXT",
            "GDP",
            "HEALTH_EXPENDITURE",
            "SNAPSHOT_ID",
        ):
            self.assertNotIn(forbidden, repository_source.upper())
            self.assertNotIn(forbidden, service_source.upper())


if __name__ == "__main__":
    unittest.main()
