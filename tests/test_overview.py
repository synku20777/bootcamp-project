from __future__ import annotations

import unittest
from datetime import date

from app.config import Settings
from app.models.covid import DashboardOverview, OverviewLocation, OverviewTotals
from app.services.cache_service import CacheService, CacheStatus
from app.services.covid_service import CovidService


class InMemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value: str, **kwargs):
        if kwargs.get("nx") and key in self.values:
            return False
        self.values[key] = value
        return True

    def setex(self, key: str, _ttl: int, value: str):
        self.values[key] = value

    def delete(self, key: str):
        self.values.pop(key, None)

    def eval(self, _script: str, _key_count: int, key: str, token: str):
        if self.values.get(key) == token:
            self.values.pop(key, None)
            return 1
        return 0


class OverviewRepository:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row
        self.calls = 0

    def fetch_overview(self) -> list[dict[str, object]]:
        self.calls += 1
        return [self.row]


def overview_row(*, extended: bool) -> dict[str, object]:
    report_date = date(2023, 3, 9) if extended else date(2020, 12, 14)
    return {
        "COUNTRY": "Micronesia" if extended else "Latvia",
        "COUNTRY_ISO2": "FM" if extended else "LV",
        "COUNTRY_ISO3": "FSM" if extended else "LVA",
        "LOCATION_KEY": "FSM" if extended else "LV",
        "REPORT_DATE": report_date,
        "COVID_RATE_POPULATION_2020": 112_106 if extended else 1_900_000,
        "DENOMINATOR_PUBLICATION_STATUS": "CANDIDATE" if extended else "ACTIVE",
        "CASES_CUMULATIVE": 23_948 if extended else 25_000,
        "DEATHS_CUMULATIVE": 61 if extended else 350,
        "CASES_PER_100K": 21_362.0 if extended else 1_315.79,
        "DEATHS_PER_100K": 54.4 if extended else 18.42,
        "MORTALITY_RATE_PERCENT": 0.2547 if extended else 1.4,
        "DENOMINATOR_JOIN_STATUS": "MATCHED",
        "DATASET_REPORT_DATE": report_date,
        "COUNTRY_COUNT": 222 if extended else 214,
        "TOTAL_CASES": 676_609_955 if extended else 71_503_614,
        "TOTAL_DEATHS": 6_881_955 if extended else 1_612_833,
        "GLOBAL_MORTALITY_RATE_PERCENT": 1.017 if extended else 2.2556,
    }


def legacy_payload() -> DashboardOverview:
    row = overview_row(extended=False)
    return DashboardOverview(
        totals=OverviewTotals(
            report_date=row["DATASET_REPORT_DATE"],
            countries=row["COUNTRY_COUNT"],
            total_cases=row["TOTAL_CASES"],
            total_deaths=row["TOTAL_DEATHS"],
            mortality_rate_percent=row["GLOBAL_MORTALITY_RATE_PERCENT"],
        ),
        locations=[
            OverviewLocation(
                country=row["COUNTRY"],
                iso2=row["COUNTRY_ISO2"],
                iso3=row["COUNTRY_ISO3"],
                location_key=row["LOCATION_KEY"],
                report_date=row["REPORT_DATE"],
                covid_rate_population_2020=row["COVID_RATE_POPULATION_2020"],
                denominator_publication_status=row["DENOMINATOR_PUBLICATION_STATUS"],
                cases_cumulative=row["CASES_CUMULATIVE"],
                deaths_cumulative=row["DEATHS_CUMULATIVE"],
                cases_per_100k=row["CASES_PER_100K"],
                deaths_per_100k=row["DEATHS_PER_100K"],
                mortality_rate_percent=row["MORTALITY_RATE_PERCENT"],
                denominator_join_status=row["DENOMINATOR_JOIN_STATUS"],
            )
        ],
    )


class OverviewServiceTests(unittest.TestCase):
    def test_dataset_and_version_isolate_extended_overview_from_legacy_cache(
        self,
    ) -> None:
        redis = InMemoryRedis()
        extended_settings = Settings(
            _env_file=None,
            cache_namespace="overview-test",
            cache_lock_wait_seconds=0,
        )
        legacy_settings = Settings(
            _env_file=None,
            covid_dataset="legacy",
            cache_namespace="overview-test",
            cache_lock_wait_seconds=0,
        )
        extended_cache = CacheService(redis, extended_settings)
        legacy_cache = CacheService(redis, legacy_settings)

        old_legacy_key, _ = extended_cache._key("overview", {"version": 1})
        redis.values[old_legacy_key] = legacy_payload().model_dump_json()

        extended_repository = OverviewRepository(overview_row(extended=True))
        extended_service = CovidService(
            extended_repository,
            extended_cache,
            extended_settings,
        )
        extended, extended_status = extended_service.overview()

        self.assertEqual(extended_status, CacheStatus.MISS)
        self.assertEqual(extended.totals.report_date, date(2023, 3, 9))
        self.assertEqual(extended.totals.countries, 222)
        self.assertEqual(extended.locations[0].iso3, "FSM")
        self.assertEqual(extended_repository.calls, 1)

        legacy_repository = OverviewRepository(overview_row(extended=False))
        legacy_service = CovidService(legacy_repository, legacy_cache, legacy_settings)
        legacy, legacy_status = legacy_service.overview()

        self.assertEqual(legacy_status, CacheStatus.MISS)
        self.assertEqual(legacy.totals.report_date, date(2020, 12, 14))
        self.assertEqual(legacy_repository.calls, 1)

        extended_key, _ = extended_cache._key(
            "overview",
            {"dataset": "extended", "version": 2},
        )
        legacy_key, _ = legacy_cache._key(
            "overview",
            {"dataset": "legacy", "version": 2},
        )
        self.assertNotEqual(extended_key, legacy_key)
        self.assertIn(extended_key, redis.values)
        self.assertIn(legacy_key, redis.values)

        repeated, repeated_status = extended_service.overview()
        self.assertEqual(repeated_status, CacheStatus.HIT)
        self.assertEqual(repeated, extended)
        self.assertEqual(extended_repository.calls, 1)


if __name__ == "__main__":
    unittest.main()
