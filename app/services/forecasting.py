from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil, sqrt
from statistics import fmean

from app.models.covid import ForecastModel

MOVING_AVERAGE_WINDOW = 7
TREND_WINDOW = 42
HOLDOUT_OBSERVATIONS = 14
MINIMUM_OBSERVATIONS = 42
INTERVAL_LEVEL_PERCENT = 90


@dataclass(frozen=True)
class ModelScores:
    mae: float
    rmse: float
    absolute_errors: tuple[float, ...]


@dataclass(frozen=True)
class ForecastValue:
    report_date: date
    predicted: float
    lower_bound: float
    upper_bound: float


@dataclass(frozen=True)
class ForecastComputation:
    selected_model: ForecastModel
    moving_average: ModelScores
    linear_trend: ModelScores
    holdout_start_date: date
    holdout_observations: int
    forecast: tuple[ForecastValue, ...]


def _non_negative(value: float) -> float:
    return max(0.0, value)


def _moving_average(values: list[float]) -> float:
    return fmean(values[-MOVING_AVERAGE_WINDOW:])


def _linear_trend(
    observations: list[tuple[date, float]],
    target_date: date,
) -> float:
    window = observations[-TREND_WINDOW:]
    origin = window[0][0]
    x_values = [(observed_date - origin).days for observed_date, _ in window]
    y_values = [value for _, value in window]
    x_mean = fmean(x_values)
    y_mean = fmean(y_values)
    denominator = sum((value - x_mean) ** 2 for value in x_values)
    slope = (
        sum(
            (x_value - x_mean) * (y_value - y_mean)
            for x_value, y_value in zip(x_values, y_values, strict=True)
        )
        / denominator
        if denominator
        else 0.0
    )
    target_x = (target_date - origin).days
    return _non_negative(y_mean + slope * (target_x - x_mean))


def _scores(actual: list[float], predicted: list[float]) -> ModelScores:
    errors = tuple(
        abs(actual_value - predicted_value)
        for actual_value, predicted_value in zip(actual, predicted, strict=True)
    )
    squared_errors = [
        (actual_value - predicted_value) ** 2
        for actual_value, predicted_value in zip(actual, predicted, strict=True)
    ]
    return ModelScores(
        mae=fmean(errors),
        rmse=sqrt(fmean(squared_errors)),
        absolute_errors=errors,
    )


def _rolling_validation(
    observations: list[tuple[date, float]],
) -> tuple[ModelScores, ModelScores, date]:
    holdout_size = min(HOLDOUT_OBSERVATIONS, len(observations) // 3)
    split_index = len(observations) - holdout_size
    actual: list[float] = []
    moving_predictions: list[float] = []
    trend_predictions: list[float] = []

    # Rolling-origin validation preserves causality. A random split would let later
    # pandemic regimes leak into earlier predictions and understate real error.
    for index in range(split_index, len(observations)):
        prior = observations[:index]
        target_date, target_value = observations[index]
        actual.append(target_value)
        moving_predictions.append(_non_negative(_moving_average([v for _, v in prior])))
        trend_predictions.append(_linear_trend(prior, target_date))

    return (
        _scores(actual, moving_predictions),
        _scores(actual, trend_predictions),
        observations[split_index][0],
    )


def _empirical_error_margin(scores: ModelScores) -> float:
    ordered = sorted(scores.absolute_errors)
    # A nearest-rank empirical interval is honest for this baseline: it avoids
    # claiming normally distributed residuals that case-reporting data do not have.
    index = max(0, ceil((INTERVAL_LEVEL_PERCENT / 100) * len(ordered)) - 1)
    return max(ordered[index], scores.rmse)


def compute_forecast(
    observations: list[tuple[date, float]],
    horizon_days: int,
) -> ForecastComputation:
    if len(observations) < MINIMUM_OBSERVATIONS:
        raise ValueError(
            f"Forecasting requires at least {MINIMUM_OBSERVATIONS} observations."
        )

    ordered = sorted(observations, key=lambda item: item[0])
    moving_scores, trend_scores, holdout_start = _rolling_validation(ordered)

    # The weekly mean wins exact ties because extra slope is unjustified unless it
    # produces a measurable temporal-validation improvement.
    selected_model = (
        ForecastModel.LINEAR_TREND
        if trend_scores.mae < moving_scores.mae
        else ForecastModel.SEVEN_DAY_MEAN
    )
    selected_scores = (
        trend_scores if selected_model == ForecastModel.LINEAR_TREND else moving_scores
    )
    base_margin = _empirical_error_margin(selected_scores)
    last_date = ordered[-1][0]
    values = [value for _, value in ordered]
    forecast: list[ForecastValue] = []

    for offset in range(1, horizon_days + 1):
        forecast_date = last_date + timedelta(days=offset)
        prediction = (
            _linear_trend(ordered, forecast_date)
            if selected_model == ForecastModel.LINEAR_TREND
            else _non_negative(_moving_average(values))
        )
        # Uncertainty grows with horizon because all candidates are evaluated as
        # one-step forecasts; the square-root rule avoids false precision without
        # letting the interval explode linearly.
        margin = base_margin * sqrt(1 + (offset - 1) / MOVING_AVERAGE_WINDOW)
        forecast.append(
            ForecastValue(
                report_date=forecast_date,
                predicted=round(prediction, 3),
                lower_bound=round(_non_negative(prediction - margin), 3),
                upper_bound=round(prediction + margin, 3),
            )
        )

    return ForecastComputation(
        selected_model=selected_model,
        moving_average=moving_scores,
        linear_trend=trend_scores,
        holdout_start_date=holdout_start,
        holdout_observations=len(selected_scores.absolute_errors),
        forecast=tuple(forecast),
    )
