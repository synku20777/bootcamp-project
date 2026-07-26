from __future__ import annotations

import unittest
from datetime import date

from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from app.dependencies import (
    get_covid_service,
    get_redis_client,
    get_snowflake_repository,
)
from app.exceptions import DataSourceUnavailableError
from app.main import app
from app.models.covid import (
    ComparisonSeries,
    CountryComparison,
    CountryDashboard,
    CountrySummary,
    DashboardComparison,
    DashboardComparisonSeries,
    DashboardOverview,
    Metric,
    MetricPoint,
    MetricSeries,
    OverviewLocation,
    OverviewTotals,
)
from app.services.cache_service import CacheStatus

LATVIA_SUMMARY = CountrySummary(
    country="Latvia",
    iso2="LV",
    iso3="LVA",
    location_key="LVA",
    report_date=date(2020, 12, 14),
    population=1_900_000,
    cases_cumulative=25_000,
    deaths_cumulative=350,
    cases_per_100k=1315.79,
    deaths_per_100k=18.42,
    mortality_rate_percent=1.4,
)


class FakeCovidService:
    def overview(self):
        location = OverviewLocation(
            **LATVIA_SUMMARY.model_dump(),
            population_join_status="MATCHED",
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
            ),
            CacheStatus.MISS,
        )

    def dashboard_comparison(self, _identifiers, start_date, end_date):
        point = MetricPoint(report_date=date(2020, 3, 1), value=1.2)

        def series(country, iso2, iso3, location_key, points):
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
            )

        return (
            DashboardComparison(
                start_date=start_date,
                end_date=end_date,
                series=[
                    series("Latvia", "LV", "LVA", "LVA", [point]),
                    series("Estonia", "EE", "EST", "EST", []),
                ],
                countries_without_data=["Estonia"],
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
        raise DataSourceUnavailableError("Snowflake")


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
        self.assertEqual(repository.calls, 1)

        app.dependency_overrides[get_snowflake_repository] = (
            lambda: UnavailableSnowflakeRepository()
        )
        with TestClient(app) as client:
            failure = client.get("/health/snowflake")

        self.assertEqual(failure.status_code, 503)
        self.assertNotIn("connector", failure.text.lower())
        self.assertNotIn("sql", failure.text.lower())

    def test_analytical_contracts_and_cache_header(self) -> None:
        app.dependency_overrides[get_covid_service] = lambda: FakeCovidService()
        with TestClient(app) as client:
            overview = client.get("/dashboard/overview")
            summary = client.get("/countries/LV/summary")
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

        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.headers["X-Cache"], "MISS")
        self.assertEqual(overview.json()["totals"]["total_cases"], 25_000)
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["iso2"], "LV")
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
        self.assertEqual(repository.calls, 0)
        self.assertNotIn("private infrastructure detail", response.text)


if __name__ == "__main__":
    unittest.main()
