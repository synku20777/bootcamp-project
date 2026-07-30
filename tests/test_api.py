from __future__ import annotations

import unittest
from datetime import date

from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from app.dependencies import (
    get_covid_service,
    get_mongo_client,
    get_redis_client,
    get_snowflake_repository,
)
from app.exceptions import DataSourceUnavailableError
from app.main import app
from app.models.covid import (
    CaseIncreasePattern,
    CaseIncreasePatterns,
    CaseIncreasePatternSummary,
    ComparisonSeries,
    ContextChange,
    ContextIndicator,
    CountryComparison,
    CountryContext,
    CountryDashboard,
    CountryForecast,
    CountrySummary,
    DashboardComparison,
    DashboardComparisonSeries,
    DashboardOverview,
    ForecastEvaluation,
    ForecastMetric,
    ForecastModel,
    ForecastPoint,
    Metric,
    MetricPoint,
    MetricSeries,
    OverviewLocation,
    OverviewTotals,
    WorldBankBaselineContext,
)
from app.services.cache_service import CacheStatus

LATVIA_SUMMARY = CountrySummary(
    country="Latvia",
    iso2="LV",
    iso3="LVA",
    location_key="LVA",
    report_date=date(2020, 12, 14),
    covid_rate_population_2020=1_900_000,
    cases_cumulative=25_000,
    deaths_cumulative=350,
    cases_per_100k=1315.79,
    deaths_per_100k=18.42,
    mortality_rate_percent=1.4,
)

SNAPSHOT_ID = "wdi2-2019-2021-372906f371e0391f"


def context_indicator(
    value: float | int | None,
    year: int,
    unit: str,
    code: str,
) -> ContextIndicator:
    return ContextIndicator(
        value=value,
        status="available" if value is not None else "missing",
        year=year,
        unit=unit,
        indicator_code=code,
        snapshot_id=SNAPSHOT_ID,
    )


LATVIA_CONTEXT = CountryContext(
    country="Latvia",
    iso2="LV",
    iso3="LVA",
    location_key="LVA",
    population_2020_context=context_indicator(1_901_548, 2020, "people", "SP.POP.TOTL"),
    covid_rate_population_2020=context_indicator(
        1_900_000, 2020, "people", "SP.POP.TOTL"
    ),
    population_density_2019=context_indicator(
        30.4, 2019, "people per sq. km of land area", "EN.POP.DNST"
    ),
    population_age_65_plus_pct_2019=context_indicator(
        20.3, 2019, "% of total population", "SP.POP.65UP.TO.ZS"
    ),
    real_gdp_per_capita_2019=context_indicator(
        18_000, 2019, "constant 2015 US$", "NY.GDP.PCAP.KD"
    ),
    health_expenditure_per_capita_ppp_2019=context_indicator(
        2_200, 2019, "current international $", "SH.XPD.CHEX.PP.CD"
    ),
    real_gdp_per_capita_annual=[
        context_indicator(18_000, 2019, "constant 2015 US$", "NY.GDP.PCAP.KD"),
        context_indicator(17_500, 2020, "constant 2015 US$", "NY.GDP.PCAP.KD"),
        context_indicator(18_200, 2021, "constant 2015 US$", "NY.GDP.PCAP.KD"),
    ],
    real_gdp_per_capita_change_2020_vs_2019=ContextChange(
        value=-2.78, status="available", baseline_year=2019, comparison_year=2020
    ),
    real_gdp_per_capita_change_2021_vs_2019=ContextChange(
        value=1.11, status="available", baseline_year=2019, comparison_year=2021
    ),
    real_gdp_per_capita_change_2021_vs_2020=ContextChange(
        value=4.0, status="available", baseline_year=2020, comparison_year=2021
    ),
    covid_latest_report_date=date(2020, 12, 14),
    snapshot_id=SNAPSHOT_ID,
)

LATVIA_BASELINE_CONTEXT = WorldBankBaselineContext(
    **LATVIA_CONTEXT.model_dump(
        include={
            "population_2020_context",
            "population_density_2019",
            "population_age_65_plus_pct_2019",
            "real_gdp_per_capita_2019",
            "health_expenditure_per_capita_ppp_2019",
            "snapshot_id",
        }
    )
)


class FakeCovidService:
    def overview(self):
        location = OverviewLocation(
            **LATVIA_SUMMARY.model_dump(),
            denominator_join_status="MATCHED",
        )
        return (
            DashboardOverview(
                totals=OverviewTotals(
                    report_date=date(2020, 12, 14),
                    countries=1,
                    total_cases=25_000,
                    total_deaths=350,
                    mortality_rate_percent=1.4,
                ),
                locations=[location],
            ),
            CacheStatus.MISS,
        )

    def summary(self, _identifier):
        return LATVIA_SUMMARY, CacheStatus.MISS

    def case_increase_patterns(
        self,
        _country,
        start_date,
        end_date,
        minimum_consecutive_increases,
        _limit,
    ):
        pattern = CaseIncreasePattern(
            country="Micronesia",
            iso2="FM",
            iso3="FSM",
            location_key="FSM",
            start_date=date(2022, 8, 1),
            end_date=date(2022, 8, 5),
            days_in_pattern=5,
            consecutive_increases=4,
            start_cases=2,
            end_cases=40,
        )
        return (
            CaseIncreasePatterns(
                start_date=start_date,
                end_date=end_date,
                minimum_consecutive_increases=minimum_consecutive_increases,
                returned_patterns=1,
                summary=CaseIncreasePatternSummary(
                    total_patterns=1,
                    countries_with_patterns=1,
                    longest_consecutive_increases=4,
                    latest_pattern_end_date=date(2022, 8, 5),
                ),
                patterns=[pattern],
            ),
            CacheStatus.MISS,
        )

    def context(self, _identifier):
        return LATVIA_CONTEXT, CacheStatus.MISS

    def compare(self, _identifiers, metric, start_date, end_date):
        return (
            CountryComparison(
                metric=metric,
                start_date=start_date,
                end_date=end_date,
                series=[
                    ComparisonSeries(
                        country="Latvia",
                        iso2="LV",
                        iso3="LVA",
                        location_key="LVA",
                        points=[
                            MetricPoint(
                                report_date=date(2020, 3, 1),
                                value=1.2,
                            )
                        ],
                    ),
                    ComparisonSeries(
                        country="Estonia",
                        iso2="EE",
                        iso3="EST",
                        location_key="EST",
                        points=[],
                    ),
                ],
                countries_without_data=["Estonia"],
            ),
            CacheStatus.MISS,
        )

    def country_dashboard(self, _identifier, metric, start_date, end_date):
        point = MetricPoint(report_date=date(2020, 3, 1), value=1.2)
        return (
            CountryDashboard(
                **LATVIA_SUMMARY.model_dump(
                    include={"country", "iso2", "iso3", "location_key"}
                ),
                start_date=start_date,
                end_date=end_date,
                summary=LATVIA_SUMMARY,
                selected=MetricSeries(metric=metric, points=[point]),
                daily_cases=MetricSeries(metric=Metric.NEW_CASES, points=[point]),
                daily_deaths=MetricSeries(metric=Metric.NEW_DEATHS, points=[point]),
                mortality=MetricSeries(
                    metric=Metric.MORTALITY_RATE_PERCENT,
                    points=[point],
                ),
                context=LATVIA_CONTEXT,
            ),
            CacheStatus.MISS,
        )

    def dashboard_comparison(self, _identifiers, start_date, end_date):
        point = MetricPoint(report_date=date(2020, 3, 1), value=1.2)

        def series(country, iso2, iso3, location_key, points, context=None):
            return DashboardComparisonSeries(
                country=country,
                iso2=iso2,
                iso3=iso3,
                location_key=location_key,
                cases_per_100k=MetricSeries(
                    metric=Metric.CASES_PER_100K,
                    points=points,
                ),
                deaths_per_100k=MetricSeries(
                    metric=Metric.DEATHS_PER_100K,
                    points=points,
                ),
                mortality=MetricSeries(
                    metric=Metric.MORTALITY_RATE_PERCENT,
                    points=points,
                ),
                world_bank_context=context,
                world_bank_context_status=(
                    "available" if context else "context_data_unavailable"
                ),
            )

        return (
            DashboardComparison(
                start_date=start_date,
                end_date=end_date,
                series=[
                    series(
                        "Latvia",
                        "LV",
                        "LVA",
                        "LVA",
                        [point],
                        LATVIA_BASELINE_CONTEXT,
                    ),
                    series("Estonia", "EE", "EST", "EST", []),
                ],
                countries_without_data=["Estonia"],
            ),
            CacheStatus.MISS,
        )

    def forecast(self, _identifier, metric, horizon_days, lookback_days):
        return (
            CountryForecast(
                country="Latvia",
                iso2="LV",
                iso3="LVA",
                location_key="LVA",
                metric=metric,
                historical_start_date=date(2020, 9, 16),
                historical_end_date=date(2020, 12, 14),
                horizon_days=horizon_days,
                lookback_days=lookback_days,
                training_observations=90,
                interval_level_percent=90,
                history=[MetricPoint(report_date=date(2020, 12, 14), value=500)],
                forecast=[
                    ForecastPoint(
                        report_date=date(2020, 12, 15),
                        predicted=510,
                        lower_bound=450,
                        upper_bound=570,
                    )
                ],
                evaluation=ForecastEvaluation(
                    holdout_start_date=date(2020, 12, 1),
                    holdout_observations=14,
                    moving_average_mae=80,
                    moving_average_rmse=100,
                    linear_trend_mae=70,
                    linear_trend_rmse=90,
                    selected_model=ForecastModel.LINEAR_TREND,
                    selected_mae=70,
                    selected_rmse=90,
                ),
                caveats=["Historical demonstration only."],
            ),
            CacheStatus.MISS,
        )


class HealthySnowflakeRepository:
    calls = 0

    def check_health(self):
        self.calls += 1
        return [{}]


class UnavailableSnowflakeRepository:
    def check_health(self):
        raise DataSourceUnavailableError(
            "Snowflake",
            code="snowflake_unavailable",
        )


class UnavailableRedis:
    def get(self, _key):
        raise RedisError("private infrastructure detail")


class CountingSummaryRepository:
    calls = 0

    def fetch_summary(self, _identifier):
        self.calls += 1
        return []

    def fetch_country_dashboard(self, *_args):
        self.calls += 1
        return []


class ApiTests(unittest.TestCase):
    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_liveness_has_request_id_without_snowflake_dependency(self) -> None:
        def dependency_must_not_run():
            raise AssertionError("Snowflake dependency was initialized")

        app.dependency_overrides[get_snowflake_repository] = dependency_must_not_run
        app.dependency_overrides[get_mongo_client] = dependency_must_not_run
        app.dependency_overrides[get_redis_client] = dependency_must_not_run
        with TestClient(app) as client:
            response = client.get("/health/live")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertTrue(response.headers["X-Request-ID"])

    def test_snowflake_health_success_and_sanitized_failure(self) -> None:
        repository = HealthySnowflakeRepository()
        app.dependency_overrides[get_snowflake_repository] = lambda: repository
        with TestClient(app) as client:
            success = client.get("/health/snowflake")

        self.assertEqual(success.status_code, 200)
        self.assertEqual(success.json()["status"], "connected")
        self.assertEqual(
            success.json()["objects"]["CASE_INCREASE_PATTERNS"],
            "accessible",
        )
        self.assertEqual(repository.calls, 1)

        app.dependency_overrides[get_snowflake_repository] = lambda: (
            UnavailableSnowflakeRepository()
        )
        with TestClient(app) as client:
            failure = client.get("/health/snowflake")

        self.assertEqual(failure.status_code, 503)
        self.assertEqual(
            failure.json()["error"]["code"],
            "snowflake_unavailable",
        )
        self.assertEqual(
            failure.json()["error"]["request_id"],
            failure.headers["X-Request-ID"],
        )
        self.assertNotIn("connector", failure.text.lower())
        self.assertNotIn("sql", failure.text.lower())

    def test_analytical_contracts_and_cache_header(self) -> None:
        app.dependency_overrides[get_covid_service] = lambda: FakeCovidService()
        with TestClient(app) as client:
            overview = client.get("/dashboard/overview")
            summary = client.get("/countries/LV/summary")
            context = client.get("/countries/LV/context")
            comparison = client.get(
                "/compare",
                params=[
                    ("country", "LV"),
                    ("country", "EE"),
                    ("metric", "cases_per_100k"),
                    ("start_date", "2020-03-01"),
                    ("end_date", "2020-12-14"),
                ],
            )
            country_page = client.get(
                "/dashboard/countries/LV",
                params={
                    "metric": "cases_per_100k",
                    "start_date": "2020-03-01",
                    "end_date": "2020-12-14",
                },
            )
            comparison_page = client.get(
                "/dashboard/compare",
                params=[
                    ("country", "LV"),
                    ("country", "EE"),
                    ("start_date", "2020-03-01"),
                    ("end_date", "2020-12-14"),
                ],
            )
            forecast = client.get(
                "/forecast",
                params={
                    "country": "LV",
                    "metric": ForecastMetric.NEW_CASES.value,
                    "days": 30,
                    "lookback_days": 90,
                },
            )
            patterns = client.get(
                "/patterns/case-increases",
                params={
                    "country": "FSM",
                    "start_date": "2022-01-01",
                    "end_date": "2023-03-09",
                    "minimum_consecutive_increases": 3,
                    "limit": 100,
                },
            )

        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.headers["X-Cache"], "MISS")
        self.assertEqual(overview.json()["totals"]["total_cases"], 25_000)
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["iso2"], "LV")
        self.assertEqual(
            summary.json()["denominator_publication_status"],
            "ACTIVE",
        )
        self.assertEqual(context.status_code, 200)
        self.assertEqual(context.json()["methodology"]["classification"], "descriptive")
        self.assertEqual(
            context.json()["population_2020_context"]["indicator_code"],
            "SP.POP.TOTL",
        )
        self.assertEqual(comparison.status_code, 200)
        self.assertEqual(
            comparison.json()["countries_without_data"],
            ["Estonia"],
        )
        self.assertEqual(country_page.status_code, 200)
        self.assertEqual(country_page.headers["X-Cache"], "MISS")
        self.assertEqual(country_page.json()["daily_cases"]["metric"], "new_cases")
        self.assertEqual(comparison_page.status_code, 200)
        self.assertEqual(
            comparison_page.json()["countries_without_data"],
            ["Estonia"],
        )
        comparison_series = comparison_page.json()["series"]
        self.assertEqual(
            comparison_series[0]["world_bank_context"]["population_2020_context"][
                "indicator_code"
            ],
            "SP.POP.TOTL",
        )
        self.assertEqual(
            comparison_series[0]["world_bank_context"]["snapshot_id"],
            SNAPSHOT_ID,
        )
        self.assertEqual(
            comparison_series[0]["world_bank_context_status"],
            "available",
        )
        self.assertIsNone(comparison_series[1]["world_bank_context"])
        self.assertEqual(
            comparison_series[1]["world_bank_context_status"],
            "context_data_unavailable",
        )
        self.assertEqual(forecast.status_code, 200)
        self.assertEqual(forecast.headers["X-Cache"], "MISS")
        self.assertEqual(
            forecast.json()["evaluation"]["selected_model"], "linear_trend"
        )
        self.assertEqual(patterns.status_code, 200)
        self.assertEqual(patterns.headers["X-Cache"], "MISS")
        self.assertEqual(patterns.json()["patterns"][0]["iso3"], "FSM")
        self.assertNotIn("source_name", patterns.text.lower())
        self.assertNotIn("series_segment", patterns.text.lower())

    def test_pattern_parameter_bounds_are_validated(self) -> None:
        app.dependency_overrides[get_covid_service] = lambda: FakeCovidService()
        with TestClient(app) as client:
            too_short = client.get(
                "/patterns/case-increases",
                params={
                    "start_date": "2020-03-01",
                    "end_date": "2023-03-09",
                    "minimum_consecutive_increases": 2,
                },
            )
            too_large = client.get(
                "/patterns/case-increases",
                params={
                    "start_date": "2020-03-01",
                    "end_date": "2023-03-09",
                    "limit": 201,
                },
            )

        self.assertEqual(too_short.status_code, 422)
        self.assertEqual(too_large.status_code, 422)

    def test_country_dashboard_redis_failure_skips_snowflake(self) -> None:
        repository = CountingSummaryRepository()
        app.dependency_overrides[get_redis_client] = lambda: UnavailableRedis()
        app.dependency_overrides[get_snowflake_repository] = lambda: repository

        with TestClient(app) as client:
            response = client.get(
                "/dashboard/countries/LV",
                params={
                    "metric": "cases_per_100k",
                    "start_date": "2020-03-01",
                    "end_date": "2020-12-14",
                },
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "cache_unavailable")
        self.assertEqual(repository.calls, 0)

    def test_invalid_comparison_is_422(self) -> None:
        app.dependency_overrides[get_covid_service] = lambda: FakeCovidService()
        with TestClient(app) as client:
            response = client.get(
                "/compare",
                params={
                    "country": "LV",
                    "metric": "cases_per_100k",
                    "start_date": "2020-03-01",
                    "end_date": "2020-12-14",
                },
            )

        self.assertEqual(response.status_code, 422)

    def test_redis_failure_returns_503_without_snowflake_query(self) -> None:
        repository = CountingSummaryRepository()
        app.dependency_overrides[get_redis_client] = lambda: UnavailableRedis()
        app.dependency_overrides[get_snowflake_repository] = lambda: repository

        with TestClient(app) as client:
            response = client.get("/countries/LV/summary")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "cache_unavailable")
        self.assertEqual(repository.calls, 0)
        self.assertNotIn("private infrastructure detail", response.text)


if __name__ == "__main__":
    unittest.main()
