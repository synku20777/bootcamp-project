from __future__ import annotations

import unittest
from datetime import date, timedelta

from app.config import Settings
from app.models.covid import ForecastMetric, ForecastModel
from app.services.cache_service import CacheStatus
from app.services.covid_service import CovidService
from app.services.forecasting import compute_forecast


class ImmediateCache:
    def __init__(self) -> None:
        self.call = None

    def get_or_compute(self, *, compute, **_kwargs):
        self.call = _kwargs
        return compute(), CacheStatus.MISS


class ForecastRepository:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def fetch_forecast_history(self, _identifier, _metric, _lookback_days):
        self.calls += 1
        return self.rows


class ForecastingTests(unittest.TestCase):
    def test_linear_growth_selects_trend_with_temporal_holdout(self) -> None:
        start = date(2020, 1, 1)
        observations = [
            (start + timedelta(days=index), float(10 + 2 * index))
            for index in range(60)
        ]

        result = compute_forecast(observations, horizon_days=7)

        self.assertEqual(result.selected_model, ForecastModel.LINEAR_TREND)
        self.assertAlmostEqual(result.linear_trend.mae, 0.0, places=6)
        self.assertAlmostEqual(result.forecast[0].predicted, 130.0, places=3)
        self.assertEqual(result.forecast[-1].report_date, date(2020, 3, 7))
        self.assertTrue(
            all(point.lower_bound >= 0 for point in result.forecast),
        )

    def test_equal_error_prefers_simpler_weekly_mean(self) -> None:
        start = date(2020, 1, 1)
        observations = [(start + timedelta(days=index), 25.0) for index in range(42)]

        result = compute_forecast(observations, horizon_days=3)

        self.assertEqual(result.selected_model, ForecastModel.SEVEN_DAY_MEAN)
        self.assertEqual([point.predicted for point in result.forecast], [25.0] * 3)

    def test_short_history_is_rejected(self) -> None:
        observations = [
            (date(2020, 1, 1) + timedelta(days=index), float(index))
            for index in range(41)
        ]

        with self.assertRaisesRegex(ValueError, "at least 42 observations"):
            compute_forecast(observations, horizon_days=7)

    def test_service_preserves_source_corrections_but_models_non_negative(self) -> None:
        start = date(2023, 1, 27)
        rows = [
            {
                "COUNTRY": "Latvia",
                "COUNTRY_ISO2": "LV",
                "COUNTRY_ISO3": "LVA",
                "LOCATION_KEY": "ISO2:LV",
                "REPORT_DATE": start + timedelta(days=index),
                "METRIC_VALUE": -5 if index == 41 else 20,
            }
            for index in range(42)
        ]
        repository = ForecastRepository(rows)
        cache = ImmediateCache()
        service = CovidService(
            repository,
            cache,
            Settings(_env_file=None),
        )

        result, cache_status = service.forecast(
            "lv",
            ForecastMetric.NEW_CASES,
            horizon_days=7,
            lookback_days=42,
        )

        self.assertEqual(repository.calls, 1)
        self.assertEqual(cache_status, CacheStatus.MISS)
        self.assertEqual(result.history[-1].value, -5)
        self.assertTrue(all(point.predicted >= 0 for point in result.forecast))
        self.assertTrue(any("corrections" in caveat for caveat in result.caveats))
        self.assertTrue(
            any(
                "selected series ends on 9 March 2023" in caveat
                for caveat in result.caveats
            )
        )
        self.assertFalse(any("ends in 2020" in caveat for caveat in result.caveats))
        self.assertEqual(
            cache.call["key_payload"],
            {
                "dataset": "extended",
                "identifier": "LV",
                "metric": "new_cases",
                "horizon_days": 7,
                "lookback_days": 42,
                "version": 2,
            },
        )

    def test_caveat_uses_actual_end_date_for_legacy_series(self) -> None:
        start = date(2020, 11, 3)
        rows = [
            {
                "COUNTRY": "Latvia",
                "COUNTRY_ISO2": "LV",
                "COUNTRY_ISO3": "LVA",
                "LOCATION_KEY": "LV",
                "REPORT_DATE": start + timedelta(days=index),
                "METRIC_VALUE": 20,
            }
            for index in range(42)
        ]
        cache = ImmediateCache()
        service = CovidService(
            ForecastRepository(rows),
            cache,
            Settings(_env_file=None, covid_dataset="legacy"),
        )

        result, _ = service.forecast(
            "LV",
            ForecastMetric.NEW_CASES,
            horizon_days=7,
            lookback_days=42,
        )

        self.assertEqual(result.historical_end_date, date(2020, 12, 14))
        self.assertTrue(
            any(
                "selected series ends on 14 December 2020" in caveat
                for caveat in result.caveats
            )
        )
        self.assertFalse(any("ends in 2020" in caveat for caveat in result.caveats))
        self.assertEqual(cache.call["key_payload"]["dataset"], "legacy")
        self.assertEqual(cache.call["key_payload"]["version"], 2)

    def test_context_values_cannot_change_forecast_output(self) -> None:
        start = date(2020, 1, 1)
        rows = [
            {
                "COUNTRY": "Latvia",
                "COUNTRY_ISO2": "LV",
                "COUNTRY_ISO3": "LVA",
                "LOCATION_KEY": "LVA",
                "REPORT_DATE": start + timedelta(days=index),
                "METRIC_VALUE": 10 + index,
                "REAL_GDP_PER_CAPITA_2019": 1,
                "POPULATION_DENSITY_2019": 1,
                "HEALTH_EXPENDITURE_PER_CAPITA_PPP_2019": 1,
            }
            for index in range(42)
        ]
        changed = [
            {
                **row,
                "REAL_GDP_PER_CAPITA_2019": 999_999,
                "POPULATION_DENSITY_2019": 999_999,
                "HEALTH_EXPENDITURE_PER_CAPITA_PPP_2019": 999_999,
            }
            for row in rows
        ]

        first, _ = CovidService(
            ForecastRepository(rows), ImmediateCache(), Settings(_env_file=None)
        ).forecast("LVA", ForecastMetric.NEW_CASES, 7, 42)
        second, _ = CovidService(
            ForecastRepository(changed), ImmediateCache(), Settings(_env_file=None)
        ).forecast("LVA", ForecastMetric.NEW_CASES, 7, 42)

        self.assertEqual(first.forecast, second.forecast)
        self.assertEqual(first.evaluation, second.evaluation)


if __name__ == "__main__":
    unittest.main()
