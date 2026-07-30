# Analytics

## Forecasting

`GET /forecast` supports `new_cases` and `new_deaths`. The forecast horizon is 1 through 30 days.

The training window contains 42 through 180 observations. The default window contains 90 observations.

The service compares two models:

- A seven-day mean represents the recent reporting level.
- A linear trend uses no more than the latest 42 observations.

The service tests both models on the last 14 observations. Each test prediction uses only earlier data.

The model with the lower mean absolute error wins. An equal score selects the seven-day mean.

The response also reports root mean squared error. This value makes large forecast errors visible.

The error band uses the larger selected-model error value. It expands with the square root of the forecast horizon.

The error band is descriptive. It is not a clinical or probabilistic confidence interval.

Negative source corrections stay visible in the returned history. The model uses zero for a negative incident value.

The trend uses actual date offsets. It does not add zero observations for missing calendar days.

## Exploratory data analysis

Create the analytical marts before you run the EDA script.

Run:

```bash
uv run python scripts/run_eda.py
```

The script writes these files under `outputs/eda/`:

- `dataset_coverage.csv`
- `missing_population.csv`
- `data_corrections.csv`
- `latest_country_metrics.csv`

The exports use the same analytical mart as the API. This rule keeps metric definitions consistent.

