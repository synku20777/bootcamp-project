from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


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
    covid_rate_population_2020: int | None
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
    denominator_join_status: str


class DashboardOverview(BaseModel):
    totals: OverviewTotals
    locations: list[OverviewLocation]


class MetricSeries(BaseModel):
    metric: Metric
    points: list[MetricPoint]


class ContextIndicator(BaseModel):
    value: float | int | None
    status: str
    year: int
    unit: str
    indicator_code: str
    snapshot_id: str


class ContextChange(BaseModel):
    value: float | None
    status: str
    baseline_year: int
    comparison_year: int
    unit: str = "percent"


class ContextMethodology(BaseModel):
    classification: str = "descriptive"
    caveat: str = "Changes during the pandemic period do not establish causality."


class CountryContext(CountryIdentity):
    population_2020_context: ContextIndicator
    covid_rate_population_2020: ContextIndicator
    population_density_2019: ContextIndicator
    population_age_65_plus_pct_2019: ContextIndicator
    real_gdp_per_capita_2019: ContextIndicator
    health_expenditure_per_capita_ppp_2019: ContextIndicator
    real_gdp_per_capita_annual: list[ContextIndicator]
    real_gdp_per_capita_change_2020_vs_2019: ContextChange
    real_gdp_per_capita_change_2021_vs_2019: ContextChange
    real_gdp_per_capita_change_2021_vs_2020: ContextChange
    covid_latest_report_date: date | None
    snapshot_id: str
    methodology: ContextMethodology = Field(default_factory=ContextMethodology)


class CountryDashboard(CountryIdentity):
    start_date: date
    end_date: date
    summary: CountrySummary
    selected: MetricSeries
    daily_cases: MetricSeries
    daily_deaths: MetricSeries
    mortality: MetricSeries
    context: CountryContext | None = None
    context_status: str = "available"


class WorldBankBaselineContext(BaseModel):
    population_2020_context: ContextIndicator
    population_density_2019: ContextIndicator
    population_age_65_plus_pct_2019: ContextIndicator
    real_gdp_per_capita_2019: ContextIndicator
    health_expenditure_per_capita_ppp_2019: ContextIndicator
    snapshot_id: str


class DashboardComparisonSeries(CountryIdentity):
    cases_per_100k: MetricSeries
    deaths_per_100k: MetricSeries
    mortality: MetricSeries
    world_bank_context: WorldBankBaselineContext | None = None
    world_bank_context_status: str = "context_data_unavailable"


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
