from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel


class Metric(StrEnum):
    NEW_CASES = "new_cases"
    NEW_DEATHS = "new_deaths"
    CASES_CUMULATIVE = "cases_cumulative"
    DEATHS_CUMULATIVE = "deaths_cumulative"
    NEW_CASES_PER_100K = "new_cases_per_100k"
    NEW_DEATHS_PER_100K = "new_deaths_per_100k"
    CASES_PER_100K = "cases_per_100k"
    DEATHS_PER_100K = "deaths_per_100k"
    MORTALITY_RATE_PERCENT = "mortality_rate_percent"


class ForecastMetric(StrEnum):
    NEW_CASES = "new_cases"
    NEW_DEATHS = "new_deaths"


class ForecastModel(StrEnum):
    SEVEN_DAY_MEAN = "seven_day_mean"
    LINEAR_TREND = "linear_trend"


class CountryIdentity(BaseModel):
    country: str
    iso2: str | None
    iso3: str | None
    location_key: str


class CountrySummary(CountryIdentity):
    report_date: date
    population: int | None
    cases_cumulative: int
    deaths_cumulative: int
    cases_per_100k: float | None
    deaths_per_100k: float | None
    mortality_rate_percent: float | None


class MetricPoint(BaseModel):
    report_date: date
    value: float | int | None


class CountryTimeSeries(CountryIdentity):
    metric: Metric
    start_date: date
    end_date: date
    points: list[MetricPoint]


class ComparisonSeries(CountryIdentity):
    points: list[MetricPoint]


class CountryComparison(BaseModel):
    metric: Metric
    start_date: date
    end_date: date
    series: list[ComparisonSeries]
    countries_without_data: list[str]


class OverviewTotals(BaseModel):
    report_date: date
    countries: int
    total_cases: int
    total_deaths: int
    mortality_rate_percent: float | None


class OverviewLocation(CountrySummary):
    population_join_status: str


class DashboardOverview(BaseModel):
    totals: OverviewTotals
    locations: list[OverviewLocation]


class MetricSeries(BaseModel):
    metric: Metric
    points: list[MetricPoint]


class CountryDashboard(CountryIdentity):
    start_date: date
    end_date: date
    summary: CountrySummary
    selected: MetricSeries
    daily_cases: MetricSeries
    daily_deaths: MetricSeries
    mortality: MetricSeries


class DashboardComparisonSeries(CountryIdentity):
    cases_per_100k: MetricSeries
    deaths_per_100k: MetricSeries
    mortality: MetricSeries


class DashboardComparison(BaseModel):
    start_date: date
    end_date: date
    series: list[DashboardComparisonSeries]
    countries_without_data: list[str]


class ForecastPoint(BaseModel):
    report_date: date
    predicted: float
    lower_bound: float
    upper_bound: float


class ForecastEvaluation(BaseModel):
    holdout_start_date: date
    holdout_observations: int
    moving_average_mae: float
    moving_average_rmse: float
    linear_trend_mae: float
    linear_trend_rmse: float
    selected_model: ForecastModel
    selected_mae: float
    selected_rmse: float


class CountryForecast(CountryIdentity):
    metric: ForecastMetric
    historical_start_date: date
    historical_end_date: date
    horizon_days: int
    lookback_days: int
    training_observations: int
    interval_level_percent: int
    history: list[MetricPoint]
    forecast: list[ForecastPoint]
    evaluation: ForecastEvaluation
    caveats: list[str]
