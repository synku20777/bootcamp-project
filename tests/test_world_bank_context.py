from __future__ import annotations

import inspect
import unittest
from datetime import date

from app.exceptions import DataSourceUnavailableError
from app.repositories.snowflake_repository import SnowflakeRepository
from app.services.covid_service import CovidService
from app.world_bank_manifest import canonical_context_iso3


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
