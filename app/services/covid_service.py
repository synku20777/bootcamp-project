from __future__ import annotations

from collections import defaultdict
from datetime import date
from functools import partial
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
    ContextChange,
    ContextIndicator,
    CountryComparison,
    CountryContext,
    CountryDashboard,
    CountryForecast,
    CountryIdentity,
    CountrySummary,
    CountryTimeSeries,
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
from app.repositories.snowflake_repository import SnowflakeRepository
from app.services.cache_service import CacheService, CacheStatus
from app.services.forecasting import (
    INTERVAL_LEVEL_PERCENT,
    MINIMUM_OBSERVATIONS,
    compute_forecast,
)
from app.world_bank_manifest import canonical_context_iso3, committed_snapshot_id


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
            covid_rate_population_2020=row["COVID_RATE_POPULATION_2020"],
            denominator_publication_status=(
                row.get("DENOMINATOR_PUBLICATION_STATUS") or "ACTIVE"
            ),
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

    @staticmethod
    def _context_indicator(
        row: dict[str, Any],
        value_column: str,
        status_column: str | None,
        year: int,
        unit: str,
        indicator_code: str,
        snapshot_id: str,
    ) -> ContextIndicator:
        value = row.get(value_column)
        status = row.get(status_column) if status_column else None
        return ContextIndicator(
            value=value,
            status=status or ("available" if value is not None else "missing"),
            year=year,
            unit=unit,
            indicator_code=indicator_code,
            snapshot_id=snapshot_id,
        )

    @classmethod
    def _country_context(
        cls,
        row: dict[str, Any],
        expected_snapshot_id: str,
    ) -> CountryContext:
        active_snapshot_id = row.get("CONTEXT_SNAPSHOT_ID") or row.get("SNAPSHOT_ID")
        if active_snapshot_id != expected_snapshot_id:
            raise DataSourceUnavailableError(
                "World Bank context",
                code="context_data_unavailable",
                message="Country context data is temporarily unavailable.",
            )
        denominator_snapshot = (
            row.get("DENOMINATOR_SOURCE_SNAPSHOT_ID")
            or row.get("DENOMINATOR_VERSION")
            or "legacy-world-bank-population-2020"
        )
        identity = CountryIdentity(
            country=row.get("COUNTRY") or row["COUNTRY_NAME"],
            iso2=row.get("COUNTRY_ISO2") or row.get("ISO2"),
            iso3=row.get("COUNTRY_ISO3") or row.get("ISO3"),
            location_key=row["LOCATION_KEY"],
        )
        annual_gdp = [
            cls._context_indicator(
                row,
                f"REAL_GDP_PER_CAPITA_{year}",
                "REAL_GDP_PER_CAPITA_2019_STATUS" if year == 2019 else None,
                year,
                "constant 2015 US$",
                "NY.GDP.PCAP.KD",
                expected_snapshot_id,
            )
            for year in (2019, 2020, 2021)
        ]
        return CountryContext(
            **identity.model_dump(),
            population_2020_context=cls._context_indicator(
                row,
                "POPULATION_2020_CONTEXT",
                "POPULATION_2020_STATUS",
                2020,
                "people",
                "SP.POP.TOTL",
                expected_snapshot_id,
            ),
            covid_rate_population_2020=cls._context_indicator(
                row,
                "COVID_RATE_POPULATION_2020",
                None,
                2020,
                "people",
                "SP.POP.TOTL",
                denominator_snapshot,
            ),
            population_density_2019=cls._context_indicator(
                row,
                "POPULATION_DENSITY_2019",
                "POPULATION_DENSITY_2019_STATUS",
                2019,
                "people per sq. km of land area",
                "EN.POP.DNST",
                expected_snapshot_id,
            ),
            population_age_65_plus_pct_2019=cls._context_indicator(
                row,
                "POPULATION_AGE_65_PLUS_PCT_2019",
                "AGE_65_PLUS_2019_STATUS",
                2019,
                "% of total population",
                "SP.POP.65UP.TO.ZS",
                expected_snapshot_id,
            ),
            real_gdp_per_capita_2019=annual_gdp[0],
            health_expenditure_per_capita_ppp_2019=cls._context_indicator(
                row,
                "HEALTH_EXPENDITURE_PER_CAPITA_PPP_2019",
                "HEALTH_EXPENDITURE_PPP_2019_STATUS",
                2019,
                "current international $",
                "SH.XPD.CHEX.PP.CD",
                expected_snapshot_id,
            ),
            real_gdp_per_capita_annual=annual_gdp,
            real_gdp_per_capita_change_2020_vs_2019=ContextChange(
                value=row.get("REAL_GDP_PER_CAPITA_CHANGE_2020_VS_2019_PCT"),
                status=row.get("REAL_GDP_PER_CAPITA_CHANGE_2020_VS_2019_STATUS")
                or "missing_input",
                baseline_year=2019,
                comparison_year=2020,
            ),
            real_gdp_per_capita_change_2021_vs_2019=ContextChange(
                value=row.get("REAL_GDP_PER_CAPITA_CHANGE_2021_VS_2019_PCT"),
                status=row.get("REAL_GDP_PER_CAPITA_CHANGE_2021_VS_2019_STATUS")
                or "missing_input",
                baseline_year=2019,
                comparison_year=2021,
            ),
            real_gdp_per_capita_change_2021_vs_2020=ContextChange(
                value=row.get("REAL_GDP_PER_CAPITA_CHANGE_2021_VS_2020_PCT"),
                status=row.get("REAL_GDP_PER_CAPITA_CHANGE_2021_VS_2020_STATUS")
                or "missing_input",
                baseline_year=2020,
                comparison_year=2021,
            ),
            covid_latest_report_date=row.get("COVID_LATEST_REPORT_DATE"),
            snapshot_id=expected_snapshot_id,
        )

    @classmethod
    def _world_bank_baseline_context(
        cls,
        row: dict[str, Any],
        expected_snapshot_id: str,
    ) -> WorldBankBaselineContext:
        active_snapshot_id = row.get("CONTEXT_SNAPSHOT_ID") or row.get("SNAPSHOT_ID")
        if active_snapshot_id != expected_snapshot_id:
            raise DataSourceUnavailableError(
                "World Bank context",
                code="context_data_unavailable",
                message="Country context data is temporarily unavailable.",
            )
        return WorldBankBaselineContext(
            population_2020_context=cls._context_indicator(
                row,
                "POPULATION_2020_CONTEXT",
                "POPULATION_2020_STATUS",
                2020,
                "people",
                "SP.POP.TOTL",
                expected_snapshot_id,
            ),
            population_density_2019=cls._context_indicator(
                row,
                "POPULATION_DENSITY_2019",
                "POPULATION_DENSITY_2019_STATUS",
                2019,
                "people per sq. km of land area",
                "EN.POP.DNST",
                expected_snapshot_id,
            ),
            population_age_65_plus_pct_2019=cls._context_indicator(
                row,
                "POPULATION_AGE_65_PLUS_PCT_2019",
                "AGE_65_PLUS_2019_STATUS",
                2019,
                "% of total population",
                "SP.POP.65UP.TO.ZS",
                expected_snapshot_id,
            ),
            real_gdp_per_capita_2019=cls._context_indicator(
                row,
                "REAL_GDP_PER_CAPITA_2019",
                "REAL_GDP_PER_CAPITA_2019_STATUS",
                2019,
                "constant 2015 US$",
                "NY.GDP.PCAP.KD",
                expected_snapshot_id,
            ),
            health_expenditure_per_capita_ppp_2019=cls._context_indicator(
                row,
                "HEALTH_EXPENDITURE_PER_CAPITA_PPP_2019",
                "HEALTH_EXPENDITURE_PPP_2019_STATUS",
                2019,
                "current international $",
                "SH.XPD.CHEX.PP.CD",
                expected_snapshot_id,
            ),
            snapshot_id=expected_snapshot_id,
        )

    @classmethod
    def _normalize_comparison_identifiers(
        cls,
        identifiers: list[str],
    ) -> list[str]:
        if len(identifiers) < 2:
            raise DomainValidationError("Comparison requires 2 to 10 countries.")
        if len(identifiers) > 10:
            raise DomainValidationError("Comparison requires 2 to 10 countries.")

        normalized = [cls._identifier(value) for value in identifiers]
        if len(set(normalized)) != len(normalized):
            raise DomainValidationError("Comparison countries must be unique.")
        return normalized

    @staticmethod
    def _group_comparison_rows(
        rows: list[dict[str, Any]],
    ) -> dict[int, list[dict[str, Any]]]:
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[row["REQUEST_ORDER"]].append(row)
        return grouped

    @staticmethod
    def _resolved_country_rows(
        grouped: dict[int, list[dict[str, Any]]],
        request_order: int,
        original_identifier: str,
    ) -> list[dict[str, Any]]:
        country_rows = grouped.get(request_order, [])
        if not country_rows:
            raise CountryNotFoundError(original_identifier)
        if country_rows[0]["COUNTRY"] is None:
            raise CountryNotFoundError(original_identifier)
        return country_rows

    @staticmethod
    def _register_location(
        row: dict[str, Any],
        seen_locations: set[str],
    ) -> None:
        location_key = row["LOCATION_KEY"]
        if location_key in seen_locations:
            raise DomainValidationError(
                "Comparison identifiers resolve to the same country."
            )
        seen_locations.add(location_key)

    @staticmethod
    def _has_observations(rows: list[dict[str, Any]]) -> bool:
        return any(row["REPORT_DATE"] is not None for row in rows)

    @classmethod
    def _comparison_series(
        cls,
        country_rows: list[dict[str, Any]],
        seen_locations: set[str],
    ) -> tuple[ComparisonSeries, str | None]:
        first = country_rows[0]
        cls._register_location(first, seen_locations)
        points = cls._points(country_rows, "METRIC_VALUE")
        missing_country = first["COUNTRY"] if not points else None
        return (
            ComparisonSeries(
                **cls._identity(first),
                points=points,
            ),
            missing_country,
        )

    @classmethod
    def _dashboard_comparison_series(
        cls,
        country_rows: list[dict[str, Any]],
        seen_locations: set[str],
        expected_snapshot_id: str,
    ) -> tuple[DashboardComparisonSeries, str | None]:
        first = country_rows[0]
        cls._register_location(first, seen_locations)
        missing_country = (
            None if cls._has_observations(country_rows) else first["COUNTRY"]
        )
        try:
            world_bank_context = cls._world_bank_baseline_context(
                first,
                expected_snapshot_id,
            )
            world_bank_context_status = "available"
        except DataSourceUnavailableError:
            # WDI is optional context. Missing publication state must not turn an
            # otherwise valid multi-country COVID comparison into an outage.
            world_bank_context = None
            world_bank_context_status = "context_data_unavailable"
        return (
            DashboardComparisonSeries(
                **cls._identity(first),
                cases_per_100k=MetricSeries(
                    metric=Metric.CASES_PER_100K,
                    points=cls._points(country_rows, "CASES_PER_100K"),
                ),
                deaths_per_100k=MetricSeries(
                    metric=Metric.DEATHS_PER_100K,
                    points=cls._points(country_rows, "DEATHS_PER_100K"),
                ),
                mortality=MetricSeries(
                    metric=Metric.MORTALITY_RATE_PERCENT,
                    points=cls._points(country_rows, "MORTALITY_RATE_PERCENT"),
                ),
                world_bank_context=world_bank_context,
                world_bank_context_status=world_bank_context_status,
            ),
            missing_country,
        )

    def _load_comparison(
        self,
        identifiers: list[str],
        normalized: list[str],
        metric: Metric,
        start_date: date,
        end_date: date,
    ) -> CountryComparison:
        rows = self.repository.fetch_comparison(
            normalized,
            metric,
            start_date,
            end_date,
        )
        grouped = self._group_comparison_rows(rows)
        series: list[ComparisonSeries] = []
        seen_locations: set[str] = set()
        missing_data: list[str] = []

        for request_order, original_identifier in enumerate(identifiers):
            country_rows = self._resolved_country_rows(
                grouped,
                request_order,
                original_identifier,
            )
            country_series, missing_country = self._comparison_series(
                country_rows,
                seen_locations,
            )
            series.append(country_series)
            if missing_country is not None:
                missing_data.append(missing_country)

        return CountryComparison(
            metric=metric,
            start_date=start_date,
            end_date=end_date,
            series=series,
            countries_without_data=missing_data,
        )

    def _load_dashboard_comparison(
        self,
        identifiers: list[str],
        normalized: list[str],
        start_date: date,
        end_date: date,
        expected_snapshot_id: str,
    ) -> DashboardComparison:
        rows = self.repository.fetch_dashboard_comparison(
            normalized,
            start_date,
            end_date,
        )
        grouped = self._group_comparison_rows(rows)
        series: list[DashboardComparisonSeries] = []
        seen_locations: set[str] = set()
        missing_data: list[str] = []

        for request_order, original_identifier in enumerate(identifiers):
            country_rows = self._resolved_country_rows(
                grouped,
                request_order,
                original_identifier,
            )
            country_series, missing_country = self._dashboard_comparison_series(
                country_rows,
                seen_locations,
                expected_snapshot_id,
            )
            series.append(country_series)
            if missing_country is not None:
                missing_data.append(missing_country)

        return DashboardComparison(
            start_date=start_date,
            end_date=end_date,
            series=series,
            countries_without_data=missing_data,
        )

    def overview(self) -> tuple[DashboardOverview, CacheStatus]:
        def compute() -> DashboardOverview:
            rows = self.repository.fetch_overview()
            if not rows:
                raise DataSourceUnavailableError(
                    "Snowflake analytics",
                    code="analytics_objects_missing",
                    message="Required Snowflake analytics data has not been published.",
                )

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
                    denominator_join_status=row["DENOMINATOR_JOIN_STATUS"],
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
        normalized = self._normalize_comparison_identifiers(identifiers)
        compute = partial(
            self._load_comparison,
            identifiers,
            normalized,
            metric,
            start_date,
            end_date,
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
                covid_rate_population_2020=first["COVID_RATE_POPULATION_2020"],
                denominator_publication_status=(
                    first.get("DENOMINATOR_PUBLICATION_STATUS") or "ACTIVE"
                ),
                cases_cumulative=first["CASES_CUMULATIVE"],
                deaths_cumulative=first["DEATHS_CUMULATIVE"],
                cases_per_100k=first["CASES_PER_100K"],
                deaths_per_100k=first["DEATHS_PER_100K"],
                mortality_rate_percent=first["LATEST_MORTALITY_RATE_PERCENT"],
            )
            try:
                context = self._country_context(
                    first,
                    committed_snapshot_id(self.settings.world_bank_manifest_path),
                )
                context_status = "available"
            except DataSourceUnavailableError:
                # Context is an optional analytical layer. A manifest mismatch
                # must not turn the historical COVID page into an outage.
                context = None
                context_status = "context_data_unavailable"
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
                context=context,
                context_status=context_status,
            )

        return self.cache.get_or_compute(
            endpoint="country-page",
            key_payload={
                "identifier": normalized,
                "metric": metric.value,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "version": 2,
                "context_snapshot_id": committed_snapshot_id(
                    self.settings.world_bank_manifest_path
                ),
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
        normalized = self._normalize_comparison_identifiers(identifiers)
        snapshot_id = committed_snapshot_id(self.settings.world_bank_manifest_path)
        compute = partial(
            self._load_dashboard_comparison,
            identifiers,
            normalized,
            start_date,
            end_date,
            snapshot_id,
        )

        return self.cache.get_or_compute(
            endpoint="comparison-page",
            key_payload={
                "identifiers": normalized,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                # A context publication changes the page even when the requested
                # countries and COVID dates do not, so it belongs in cache identity.
                "version": 2,
                "context_snapshot_id": snapshot_id,
            },
            ttl_seconds=self.settings.cache_ttl_comparison_page_seconds,
            model_type=DashboardComparison,
            compute=compute,
        )

    def context(self, identifier: str) -> tuple[CountryContext, CacheStatus]:
        normalized = self._identifier(identifier)
        snapshot_id = committed_snapshot_id(self.settings.world_bank_manifest_path)
        iso3 = canonical_context_iso3(
            normalized,
            self.settings.world_bank_manifest_path,
        )

        def compute() -> CountryContext:
            rows = self.repository.fetch_country_context(iso3)
            if not rows:
                raise CountryNotFoundError(identifier)
            return self._country_context(rows[0], snapshot_id)

        return self.cache.get_or_compute(
            endpoint="country-context",
            key_payload={"iso3": iso3, "snapshot_id": snapshot_id},
            key_override=f"{snapshot_id}:country-context:{iso3}",
            ttl_seconds=self.settings.cache_ttl_country_context_seconds,
            model_type=CountryContext,
            compute=compute,
        )

    def forecast(
        self,
        identifier: str,
        metric: ForecastMetric,
        horizon_days: int,
        lookback_days: int,
    ) -> tuple[CountryForecast, CacheStatus]:
        normalized = self._identifier(identifier)
        if not 1 <= horizon_days <= 30:
            raise DomainValidationError("horizon_days must be between 1 and 30.")
        if not MINIMUM_OBSERVATIONS <= lookback_days <= 180:
            raise DomainValidationError(
                f"lookback_days must be between {MINIMUM_OBSERVATIONS} and 180."
            )

        def compute() -> CountryForecast:
            rows = self.repository.fetch_forecast_history(
                normalized,
                Metric(metric.value),
                lookback_days,
            )
            if not rows or rows[0]["COUNTRY"] is None:
                raise CountryNotFoundError(identifier)

            observed_rows = [
                row
                for row in rows
                if row["REPORT_DATE"] is not None and row["METRIC_VALUE"] is not None
            ]
            if len(observed_rows) < MINIMUM_OBSERVATIONS:
                raise DomainValidationError(
                    "Forecasting requires at least "
                    f"{MINIMUM_OBSERVATIONS} non-null daily observations."
                )

            # A negative reporting correction is not negative incidence. The API
            # returns the source value for auditability but floors only the model's
            # working copy so it cannot emit impossible negative counts.
            modelling_observations = [
                (
                    row["REPORT_DATE"],
                    max(0.0, float(row["METRIC_VALUE"])),
                )
                for row in observed_rows
            ]
            result = compute_forecast(modelling_observations, horizon_days)
            selected_scores = (
                result.linear_trend
                if result.selected_model == ForecastModel.LINEAR_TREND
                else result.moving_average
            )
            missing_days = sum(
                max(0, (current["REPORT_DATE"] - previous["REPORT_DATE"]).days - 1)
                for previous, current in zip(
                    observed_rows,
                    observed_rows[1:],
                    strict=False,
                )
            )
            caveats = [
                "The 90% interval is an empirical error band from rolling temporal "
                "validation, not a clinical or probabilistic confidence guarantee.",
                "The source is historical and ends in 2020; projections demonstrate "
                "the modelling workflow and are not current public-health guidance.",
            ]
            if any(float(row["METRIC_VALUE"]) < 0 for row in observed_rows):
                caveats.append(
                    "Negative source corrections remain visible in history but are "
                    "floored to zero for model fitting."
                )
            if missing_days:
                caveats.append(
                    f"The selected history contains {missing_days} missing calendar "
                    "days; models use actual date offsets rather than inventing zeros."
                )

            first = observed_rows[0]
            return CountryForecast(
                **self._identity(first),
                metric=metric,
                historical_start_date=first["REPORT_DATE"],
                historical_end_date=observed_rows[-1]["REPORT_DATE"],
                horizon_days=horizon_days,
                lookback_days=lookback_days,
                training_observations=len(observed_rows),
                interval_level_percent=INTERVAL_LEVEL_PERCENT,
                history=self._points(observed_rows, "METRIC_VALUE"),
                forecast=[
                    ForecastPoint(
                        report_date=point.report_date,
                        predicted=point.predicted,
                        lower_bound=point.lower_bound,
                        upper_bound=point.upper_bound,
                    )
                    for point in result.forecast
                ],
                evaluation=ForecastEvaluation(
                    holdout_start_date=result.holdout_start_date,
                    holdout_observations=result.holdout_observations,
                    moving_average_mae=round(result.moving_average.mae, 3),
                    moving_average_rmse=round(result.moving_average.rmse, 3),
                    linear_trend_mae=round(result.linear_trend.mae, 3),
                    linear_trend_rmse=round(result.linear_trend.rmse, 3),
                    selected_model=result.selected_model,
                    selected_mae=round(selected_scores.mae, 3),
                    selected_rmse=round(selected_scores.rmse, 3),
                ),
                caveats=caveats,
            )

        return self.cache.get_or_compute(
            endpoint="forecast",
            key_payload={
                "identifier": normalized,
                "metric": metric.value,
                "horizon_days": horizon_days,
                "lookback_days": lookback_days,
                "version": 1,
            },
            ttl_seconds=self.settings.cache_ttl_forecast_seconds,
            model_type=CountryForecast,
            compute=compute,
        )
