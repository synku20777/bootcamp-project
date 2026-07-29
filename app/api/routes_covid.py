from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from app.dependencies import CovidServiceDependency
from app.models.covid import (
    CountryComparison,
    CountryDashboard,
    CountryForecast,
    CountryIdentity,
    CountrySummary,
    CountryTimeSeries,
    DashboardComparison,
    DashboardOverview,
    ForecastMetric,
    Metric,
)
from app.services.cache_service import CacheStatus

router = APIRouter(tags=["covid"])


def _record_cache_status(
    request: Request,
    response: Response,
    cache_status: CacheStatus,
) -> None:
    request.state.cache_status = cache_status.value
    response.headers["X-Cache"] = cache_status.value


@router.get("/dashboard/overview", response_model=DashboardOverview)
def overview(
    request: Request,
    response: Response,
    service: CovidServiceDependency,
) -> DashboardOverview:
    result, cache_status = service.overview()
    _record_cache_status(request, response, cache_status)
    return result


@router.get(
    "/dashboard/countries/{identifier}",
    response_model=CountryDashboard,
)
def country_dashboard(
    identifier: str,
    metric: Metric,
    start_date: date,
    end_date: date,
    request: Request,
    response: Response,
    service: CovidServiceDependency,
) -> CountryDashboard:
    result, cache_status = service.country_dashboard(
        identifier,
        metric,
        start_date,
        end_date,
    )
    _record_cache_status(request, response, cache_status)
    return result


@router.get(
    "/dashboard/compare",
    response_model=DashboardComparison,
)
def dashboard_comparison(
    country: Annotated[list[str], Query(min_length=2, max_length=10)],
    start_date: date,
    end_date: date,
    request: Request,
    response: Response,
    service: CovidServiceDependency,
) -> DashboardComparison:
    result, cache_status = service.dashboard_comparison(
        country,
        start_date,
        end_date,
    )
    _record_cache_status(request, response, cache_status)
    return result


@router.get("/countries", response_model=list[CountryIdentity])
def countries(
    request: Request,
    response: Response,
    service: CovidServiceDependency,
) -> list[CountryIdentity]:
    result, cache_status = service.countries()
    _record_cache_status(request, response, cache_status)
    return result


@router.get("/countries/{identifier}/summary", response_model=CountrySummary)
def summary(
    identifier: str,
    request: Request,
    response: Response,
    service: CovidServiceDependency,
) -> CountrySummary:
    result, cache_status = service.summary(identifier)
    _record_cache_status(request, response, cache_status)
    return result


@router.get(
    "/countries/{identifier}/timeseries",
    response_model=CountryTimeSeries,
)
def timeseries(
    identifier: str,
    metric: Metric,
    start_date: date,
    end_date: date,
    request: Request,
    response: Response,
    service: CovidServiceDependency,
) -> CountryTimeSeries:
    result, cache_status = service.timeseries(
        identifier,
        metric,
        start_date,
        end_date,
    )
    _record_cache_status(request, response, cache_status)
    return result


@router.get("/compare", response_model=CountryComparison)
def compare(
    country: Annotated[list[str], Query(min_length=2, max_length=10)],
    metric: Metric,
    start_date: date,
    end_date: date,
    request: Request,
    response: Response,
    service: CovidServiceDependency,
) -> CountryComparison:
    result, cache_status = service.compare(
        country,
        metric,
        start_date,
        end_date,
    )
    _record_cache_status(request, response, cache_status)
    return result


@router.get("/forecast", response_model=CountryForecast)
def forecast(
    country: Annotated[str, Query(min_length=1)],
    request: Request,
    response: Response,
    service: CovidServiceDependency,
    metric: ForecastMetric = ForecastMetric.NEW_CASES,
    days: Annotated[int, Query(ge=1, le=30)] = 30,
    lookback_days: Annotated[int, Query(ge=42, le=180)] = 90,
) -> CountryForecast:
    result, cache_status = service.forecast(
        country,
        metric,
        days,
        lookback_days,
    )
    _record_cache_status(request, response, cache_status)
    return result
