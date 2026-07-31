# Development

Use this workflow when you run Python outside Docker. Dashboard users do not need this workflow.

## Install the environment

Run:

```bash
uv sync --locked
uv run python --version
```

Use Python 3.12 as specified by `.python-version` and `pyproject.toml`.

## Run the API

Start MongoDB and Redis before you run the API.

Run:

```bash
uv run uvicorn app.main:app --reload
```

Compose supplies service connection strings automatically. Use Compose when you do not need a host Python process.

## Change dependencies

`pyproject.toml` declares dependencies. `uv.lock` records exact resolved versions.

Add a production dependency:

```bash
uv add package-name
```

Add a development dependency:

```bash
uv add --dev development-package
```

Check and install the lockfile:

```bash
uv lock --check
uv sync --locked
```

Commit `pyproject.toml` and `uv.lock` after each dependency change. Do not edit `uv.lock` manually.

## Run code checks

Install the Git hook once:

```bash
uv run pre-commit install
```

Run all hooks:

```bash
uv run pre-commit run --all-files
```

Run individual checks:

```bash
uv run isort --check-only --diff .
uv run black --check --diff .
uv run ruff check .
uv run python -m unittest discover -s tests -v
```

Run the Spark tests after you install the Spark dependency group:

```bash
uv sync --locked --group spark
uv run --group spark python -m unittest discover -s spark_tests -v
```

GitHub Actions runs the locked environment and code checks for each push and pull request.

## Capture Snowflake performance evidence

Use this command only when you need a controlled live Snowflake measurement:

```bash
uv run python scripts/capture_snowflake_performance.py --phase post_materialization --env-file .env --output reports/snowflake/performance_evidence.json --warmups 1 --repetitions 5
```

Use `pre_materialization` for the first phase. Use the same environment file and output path for both phases.

The script disables result-cache reuse. This isolates warehouse work from cached results.

The script stores sanitized query and timing evidence. See the [Snowflake optimization evidence](../reports/snowflake/optimization_evidence_2026-07-31.md) for the method and results.

## Useful entry points

`app/dashboard/app.py` creates the Dash application. Modules under `app/dashboard/pages/` own page behavior.

`app/spark_pipeline/pipeline.py` keeps stable job entry points. Its sibling modules own individual pipeline stages.

These boundaries keep imports stable and isolate feature changes.

| Path | Purpose |
| --- | --- |
| `app/main.py` | Create the FastAPI application |
| `app/dashboard/app.py` | Create the Dash application |
| `scripts/bootstrap.py` | Run setup and diagnostics |
| `scripts/run_spark_bronze.py` | Run Spark ingestion, benchmarks, and offline clustering |
| `sql/` | Deploy Snowflake objects |
| `tests/` | Test the application |
| `spark_tests/` | Test the Spark workflow |
