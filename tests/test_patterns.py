from __future__ import annotations

import unittest
from datetime import date

from app.config import Settings
from app.exceptions import DomainValidationError
from app.services.cache_service import CacheStatus
from app.services.covid_service import CovidService


class ImmediateCache:
    def __init__(self) -> None:
        self.call: dict[str, object] | None = None

    def get_or_compute(self, **kwargs):
        self.call = kwargs
        return kwargs["compute"](), CacheStatus.MISS


class PatternRepository:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.call: tuple[object, ...] | None = None

    def fetch_case_increase_patterns(self, *args):
        self.call = args
        return self.rows


class PatternServiceTests(unittest.TestCase):
    @staticmethod
    def settings() -> Settings:
        return Settings(_env_file=None)

    def test_service_preserves_exact_summary_and_uses_all_filters_in_cache_key(
        self,
    ) -> None:
        repository = PatternRepository(
            [
                {
                    "COUNTRY": "Micronesia",
                    "COUNTRY_ISO2": "FM",
                    "COUNTRY_ISO3": "FSM",
                    "LOCATION_KEY": "FSM",
                    "START_DATE": date(2022, 8, 1),
                    "END_DATE": date(2022, 8, 5),
                    "DAYS_IN_PATTERN": 5,
                    "CONSECUTIVE_INCREASES": 4,
                    "START_CASES": 2,
                    "END_CASES": 40,
                    "TOTAL_PATTERNS": 17,
                    "COUNTRIES_WITH_PATTERNS": 6,
                    "LONGEST_CONSECUTIVE_INCREASES": 11,
                    "LATEST_PATTERN_END_DATE": date(2023, 2, 10),
                }
            ]
        )
        cache = ImmediateCache()
        service = CovidService(repository, cache, self.settings())

        result, status = service.case_increase_patterns(
            " fsm ",
            date(2022, 1, 1),
            date(2023, 3, 9),
            4,
            25,
        )

        self.assertEqual(status, CacheStatus.MISS)
        self.assertEqual(result.summary.total_patterns, 17)
        self.assertEqual(result.summary.countries_with_patterns, 6)
        self.assertEqual(result.returned_patterns, 1)
        self.assertEqual(result.patterns[0].iso3, "FSM")
        self.assertEqual(
            repository.call,
            (
                "FSM",
                date(2022, 1, 1),
                date(2023, 3, 9),
                4,
                25,
            ),
        )
        self.assertEqual(
            cache.call["key_payload"],
            {
                "dataset": "extended",
                "country": "FSM",
                "start_date": "2022-01-01",
                "end_date": "2023-03-09",
                "minimum_consecutive_increases": 4,
                "limit": 25,
                "version": 1,
            },
        )
        self.assertEqual(
            cache.call["ttl_seconds"],
            self.settings().cache_ttl_patterns_seconds,
        )

    def test_empty_pattern_result_is_a_successful_zero_summary(self) -> None:
        repository = PatternRepository([])
        service = CovidService(repository, ImmediateCache(), self.settings())

        result, _ = service.case_increase_patterns(
            None,
            date(2020, 3, 1),
            date(2023, 3, 9),
            3,
            100,
        )

        self.assertEqual(result.returned_patterns, 0)
        self.assertEqual(result.summary.total_patterns, 0)
        self.assertEqual(result.summary.countries_with_patterns, 0)
        self.assertIsNone(result.summary.longest_consecutive_increases)
        self.assertEqual(result.patterns, [])

    def test_reversed_pattern_dates_stop_before_repository_access(self) -> None:
        repository = PatternRepository([])
        service = CovidService(repository, ImmediateCache(), self.settings())

        with self.assertRaises(DomainValidationError):
            service.case_increase_patterns(
                None,
                date(2023, 3, 9),
                date(2020, 3, 1),
                3,
                100,
            )

        self.assertIsNone(repository.call)


if __name__ == "__main__":
    unittest.main()
