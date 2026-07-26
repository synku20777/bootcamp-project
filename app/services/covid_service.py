from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from pydantic import RootModel

from app.config import Settings
from app.exceptions import (
    CountryNotFoundError,
    DataSourceUnavailableError,
    DomainValidationError,
)
from app.models.covid import (
    ComparisonSeries,
    CountryComparison,
    CountryDashboard,
    CountryIdentity,
    CountrySummary,
    CountryTimeSeries,
    DashboardComparison,
    DashboardComparisonSeries,
    DashboardOverview,
    Metric,
    MetricPoint,
    MetricSeries,
    OverviewLocation,
    OverviewTotals,
)
from app.repositories.snowflake_repository import SnowflakeRepository
from app.services.cache_service import CacheService, CacheStatus


class CountryList(RootModel[list[CountryIdentity]]):
    """Serializable cache wrapper for the countries collection."""


class CovidService:
    def __init__(
        self,
        repository: SnowflakeRepository,
        cache: CacheService,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.cache = cache
        self.settings = settings

    @staticmethod
    def _identifier(value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise DomainValidationError("Country identifier cannot be empty.")
        return normalized

    @staticmethod
    def _validate_dates(start_date: date, end_date: date) -> None:
        if start_date > end_date:
            raise DomainValidationError("start_date must not be after end_date.")

    @staticmethod
    def _identity(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "country": row["COUNTRY"],
            "iso2": row["COUNTRY_ISO2"],
            "iso3": row["COUNTRY_ISO3"],
            "location_key": row["LOCATION_KEY"],
        }

    @classmethod
    def _summary(cls, row: dict[str, Any]) -> CountrySummary:
        return CountrySummary(
            **cls._identity(row),
            report_date=row["REPORT_DATE"],
            population=row["POPULATION"],
            cases_cumulative=row["CASES_CUMULATIVE"],
            deaths_cumulative=row["DEATHS_CUMULATIVE"],
            cases_per_100k=row["CASES_PER_100K"],
            deaths_per_100k=row["DEATHS_PER_100K"],
            mortality_rate_percent=row["MORTALITY_RATE_PERCENT"],
        )

    @staticmethod
    def _points(
        rows: list[dict[str, Any]],
        value_column: str,
    ) -> list[MetricPoint]:
        return [
            MetricPoint(
                report_date=row["REPORT_DATE"],
                value=row[value_column],
            )
            for row in rows
            if row["REPORT_DATE"] is not None
        ]

    def overview(self) -> tuple[DashboardOverview, CacheStatus]:
        def compute() -> DashboardOverview:
            rows = self.repository.fetch_overview()
            if not rows:
                raise DataSourceUnavailableError("Snowflake analytics")

            first = rows[0]
            totals = OverviewTotals(
                report_date=first["DATASET_REPORT_DATE"],
                countries=first["COUNTRY_COUNT"],
                total_cases=first["TOTAL_CASES"],
                total_deaths=first["TOTAL_DEATHS"],
                mortality_rate_percent=first["GLOBAL_MORTALITY_RATE_PERCENT"],
            )
            locations = [
                OverviewLocation(
                    **self._summary(row).model_dump(),
                    population_join_status=row["POPULATION_JOIN_STATUS"],
                )
                for row in rows
            ]
            return DashboardOverview(totals=totals, locations=locations)

        return self.cache.get_or_compute(
            endpoint="overview",
            key_payload={"version": 1},
            ttl_seconds=self.settings.cache_ttl_overview_seconds,
            model_type=DashboardOverview,
            compute=compute,
        )

    def countries(self) -> tuple[list[CountryIdentity], CacheStatus]:
        def compute() -> CountryList:
            return CountryList(
                root=[
                    CountryIdentity(**self._identity(row))
                    for row in self.repository.fetch_countries()
                ]
            )

        result, status = self.cache.get_or_compute(
            endpoint="countries",
            key_payload={"version": 1},
            ttl_seconds=self.settings.cache_ttl_countries_seconds,
            model_type=CountryList,
            compute=compute,
        )
        return result.root, status

    def summary(self, identifier: str) -> tuple[CountrySummary, CacheStatus]:
        normalized = self._identifier(identifier)

        def compute() -> CountrySummary:
            rows = self.repository.fetch_summary(normalized)
            if not rows:
                raise CountryNotFoundError(identifier)
            return self._summary(rows[0])

        return self.cache.get_or_compute(
            endpoint="summary",
            key_payload={"identifier": normalized, "version": 1},
            ttl_seconds=self.settings.cache_ttl_summary_seconds,
            model_type=CountrySummary,
            compute=compute,
        )

    def timeseries(
        self,
        identifier: str,
        metric: Metric,
        start_date: date,
        end_date: date,
    ) -> tuple[CountryTimeSeries, CacheStatus]:
        normalized = self._identifier(identifier)
        self._validate_dates(start_date, end_date)

        def compute() -> CountryTimeSeries:
            rows = self.repository.fetch_timeseries(
                normalized,
                metric,
                start_date,
                end_date,
            )
            if not rows:
                raise CountryNotFoundError(identifier)
            first = rows[0]
            points = [
                MetricPoint(
                    report_date=row["REPORT_DATE"],
                    value=row["METRIC_VALUE"],
                )
                for row in rows
                if row["REPORT_DATE"] is not None
            ]
            return CountryTimeSeries(
                **self._identity(first),
                metric=metric,
                start_date=start_date,
                end_date=end_date,
                points=points,
            )

        return self.cache.get_or_compute(
            endpoint="timeseries",
            key_payload={
                "identifier": normalized,
                "metric": metric.value,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "version": 1,
            },
            ttl_seconds=self.settings.cache_ttl_timeseries_seconds,
            model_type=CountryTimeSeries,
            compute=compute,
        )

    def compare(
        self,
        identifiers: list[str],
        metric: Metric,
        start_date: date,
        end_date: date,
    ) -> tuple[CountryComparison, CacheStatus]:
        self._validate_dates(start_date, end_date)
        if not 2 <= len(identifiers) <= 10:
            raise DomainValidationError("Comparison requires 2 to 10 countries.")

        normalized = [self._identifier(value) for value in identifiers]
        if len(set(normalized)) != len(normalized):
            raise DomainValidationError("Comparison countries must be unique.")

        def compute() -> CountryComparison:
            rows = self.repository.fetch_comparison(
                normalized,
                metric,
                start_date,
                end_date,
            )
            grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                grouped[row["REQUEST_ORDER"]].append(row)

            series: list[ComparisonSeries] = []
            seen_locations: set[str] = set()
            missing_data: list[str] = []
            for request_order, original_identifier in enumerate(identifiers):
                country_rows = grouped.get(request_order, [])
                if not country_rows or country_rows[0]["COUNTRY"] is None:
                    raise CountryNotFoundError(original_identifier)
                first = country_rows[0]
                location_key = first["LOCATION_KEY"]
                if location_key in seen_locations:
                    raise DomainValidationError(
                        "Comparison identifiers resolve to the same country."
                    )
                seen_locations.add(location_key)
                points = [
                    MetricPoint(
                        report_date=row["REPORT_DATE"],
                        value=row["METRIC_VALUE"],
                    )
                    for row in country_rows
                    if row["REPORT_DATE"] is not None
                ]
                if not points:
                    missing_data.append(first["COUNTRY"])
                series.append(
                    ComparisonSeries(
                        **self._identity(first),
                        points=points,
                    )
                )

            return CountryComparison(
                metric=metric,
                start_date=start_date,
                end_date=end_date,
                series=series,
                countries_without_data=missing_data,
            )

        return self.cache.get_or_compute(
            endpoint="compare",
            key_payload={
                "identifiers": normalized,
                "metric": metric.value,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "version": 1,
            },
            ttl_seconds=self.settings.cache_ttl_compare_seconds,
            model_type=CountryComparison,
            compute=compute,
        )

    def country_dashboard(
        self,
        identifier: str,
        metric: Metric,
        start_date: date,
        end_date: date,
    ) -> tuple[CountryDashboard, CacheStatus]:
        normalized = self._identifier(identifier)
        self._validate_dates(start_date, end_date)

        def compute() -> CountryDashboard:
            rows = self.repository.fetch_country_dashboard(
                normalized,
                metric,
                start_date,
                end_date,
            )
            if not rows:
                raise CountryNotFoundError(identifier)

            first = rows[0]
            summary = CountrySummary(
                **self._identity(first),
                report_date=first["LATEST_REPORT_DATE"],
                population=first["POPULATION"],
                cases_cumulative=first["CASES_CUMULATIVE"],
                deaths_cumulative=first["DEATHS_CUMULATIVE"],
                cases_per_100k=first["CASES_PER_100K"],
                deaths_per_100k=first["DEATHS_PER_100K"],
                mortality_rate_percent=first["LATEST_MORTALITY_RATE_PERCENT"],
            )
            return CountryDashboard(
                **self._identity(first),
                start_date=start_date,
                end_date=end_date,
                summary=summary,
                selected=MetricSeries(
                    metric=metric,
                    points=self._points(rows, "SELECTED_METRIC_VALUE"),
                ),
                daily_cases=MetricSeries(
                    metric=Metric.NEW_CASES,
                    points=self._points(rows, "NEW_CASES_VALUE"),
                ),
                daily_deaths=MetricSeries(
                    metric=Metric.NEW_DEATHS,
                    points=self._points(rows, "NEW_DEATHS_VALUE"),
                ),
                mortality=MetricSeries(
                    metric=Metric.MORTALITY_RATE_PERCENT,
                    points=self._points(rows, "MORTALITY_VALUE"),
                ),
            )

        return self.cache.get_or_compute(
            endpoint="country-page",
            key_payload={
                "identifier": normalized,
                "metric": metric.value,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "version": 1,
            },
            ttl_seconds=self.settings.cache_ttl_country_page_seconds,
            model_type=CountryDashboard,
            compute=compute,
        )

    def dashboard_comparison(
        self,
        identifiers: list[str],
        start_date: date,
        end_date: date,
    ) -> tuple[DashboardComparison, CacheStatus]:
        self._validate_dates(start_date, end_date)
        if not 2 <= len(identifiers) <= 10:
            raise DomainValidationError("Comparison requires 2 to 10 countries.")

        normalized = [self._identifier(value) for value in identifiers]
        if len(set(normalized)) != len(normalized):
            raise DomainValidationError("Comparison countries must be unique.")

        def compute() -> DashboardComparison:
            rows = self.repository.fetch_dashboard_comparison(
                normalized,
                start_date,
                end_date,
            )
            grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                grouped[row["REQUEST_ORDER"]].append(row)

            result_series: list[DashboardComparisonSeries] = []
            seen_locations: set[str] = set()
            missing_data: list[str] = []
            for request_order, original_identifier in enumerate(identifiers):
                country_rows = grouped.get(request_order, [])
                if not country_rows or country_rows[0]["COUNTRY"] is None:
                    raise CountryNotFoundError(original_identifier)

                first = country_rows[0]
                location_key = first["LOCATION_KEY"]
                if location_key in seen_locations:
                    raise DomainValidationError(
                        "Comparison identifiers resolve to the same country."
                    )
                seen_locations.add(location_key)

                if not any(row["REPORT_DATE"] is not None for row in country_rows):
                    missing_data.append(first["COUNTRY"])
                result_series.append(
                    DashboardComparisonSeries(
                        **self._identity(first),
                        cases_per_100k=MetricSeries(
                            metric=Metric.CASES_PER_100K,
                            points=self._points(country_rows, "CASES_PER_100K"),
                        ),
                        deaths_per_100k=MetricSeries(
                            metric=Metric.DEATHS_PER_100K,
                            points=self._points(country_rows, "DEATHS_PER_100K"),
                        ),
                        mortality=MetricSeries(
                            metric=Metric.MORTALITY_RATE_PERCENT,
                            points=self._points(
                                country_rows,
                                "MORTALITY_RATE_PERCENT",
                            ),
                        ),
                    )
                )

            return DashboardComparison(
                start_date=start_date,
                end_date=end_date,
                series=result_series,
                countries_without_data=missing_data,
            )

        return self.cache.get_or_compute(
            endpoint="comparison-page",
            key_payload={
                "identifiers": normalized,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "version": 1,
            },
            ttl_seconds=self.settings.cache_ttl_comparison_page_seconds,
            model_type=DashboardComparison,
            compute=compute,
        )
