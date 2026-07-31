# Manual setup

Use this procedure when the automatic setup cannot finish. Run each step from the repository root.

The manual workflow requires Git, Docker, and uv.

## 1. Install the tools

Install [Git](https://git-scm.com/downloads), [Docker](https://docs.docker.com/get-docker/), and [uv](https://docs.astral.sh/uv/getting-started/installation/).

On Windows, install uv:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

On macOS or Linux, install uv:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Open a new terminal. Then verify each tool:

```bash
git --version
docker --version
docker compose version
uv --version
```

## 2. Install the Python environment

Run:

```bash
uv sync --locked
uv run python --version
```

The Python version must meet `.python-version` and `pyproject.toml`.

## 3. Create the Snowflake account objects

1. Open [`sql/00_project_setup.sql`](../sql/00_project_setup.sql) in Snowsight.
2. Select the `ACCOUNTADMIN` role.
3. Run the file.

The file creates these objects:

- `COVID_PROJECT_MONITOR`, with a five-credit monthly quota.
- `COVID_WH`, with an `XSMALL` size and automatic suspension.
- `COVID_ANALYTICS`, which stores project data.
- `COVID_PROJECT_ADMIN`, which deploys project objects.
- `COVID_APP_ROLE`, which reads API marts.

The monitor sends a notification at 50 percent. It suspends the warehouse at 80 percent.

Review the quota before you run the file. The trial-account owner remains responsible for credit use.

Grant both project roles to the deployment user:

```sql
GRANT ROLE COVID_PROJECT_ADMIN TO USER YOUR_SNOWFLAKE_USERNAME;
GRANT ROLE COVID_APP_ROLE TO USER YOUR_SNOWFLAKE_USERNAME;
```

Run [`sql/00_project_objects.sql`](../sql/00_project_objects.sql) after both grants succeed.

This file creates the project schemas. It grants the MARTS read contract to `COVID_APP_ROLE`.

FastAPI uses only `COVID_APP_ROLE`. This role cannot create or replace project objects.

## 4. Inspect the Marketplace sources

Run [`sql/01_data_exploration.sql`](../sql/01_data_exploration.sql).

The queries check these conditions:

- Required columns exist.
- The date range and country coverage are known.
- Country-date duplicates are visible.
- Required values have measured null counts.
- Negative values remain visible as source corrections.
- ECDC `CASES` and `DEATHS` values are daily measures.

Do not calculate ECDC daily values with another subtraction. The source already supplies daily measures.

Confirm that both required Marketplace tables return data:

```sql
SELECT *
FROM COVID19_EPIDEMIOLOGICAL_DATA.PUBLIC.ECDC_GLOBAL
LIMIT 1;

SELECT *
FROM COVID19_EPIDEMIOLOGICAL_DATA.PUBLIC.JHU_COVID_19_TIMESERIES
LIMIT 1;
```

## 5. Create the ECDC staging layer

Run [`sql/02_create_country_mapping.sql`](../sql/02_create_country_mapping.sql).

The mapping fixes reviewed country and code exceptions. It identifies locations that do not need a population match.

Run [`sql/03_create_staging_view.sql`](../sql/03_create_staging_view.sql).

The staging view has one row for each normalized location and date. It preserves negative source corrections.

## 6. Configure the Python connection

Copy `.env.example` to `.env`. Replace the Snowflake placeholders.

```dotenv
SNOWFLAKE_ACCOUNT=organization-account-from-step-1
SNOWFLAKE_USER=your_username
SNOWFLAKE_PASSWORD=your_password
SNOWFLAKE_ROLE=COVID_PROJECT_ADMIN
SNOWFLAKE_WAREHOUSE=COVID_WH
SNOWFLAKE_DATABASE=COVID_ANALYTICS
SNOWFLAKE_SCHEMA=RAW
```

Run the configured doctor:

```bash
uv run --locked python -m scripts.bootstrap doctor --configured
```

The command does not print secret values.

## 7. Publish the World Bank context

Normal setup uses the committed WDI files. It does not contact the World Bank API.

Publish the committed snapshot:

```bash
uv run python -m scripts.world_bank_indicators publish
```

Run the verifier:

```bash
uv run python scripts/verify_world_bank_context.py
```

The legacy population file seeds the frozen denominator only when that denominator does not exist.

See [World Bank country context](architecture/world-bank-context.md) for the publication rules.

## 8. Create the analytical marts

Run these files in the given order:

1. [`sql/04_create_world_bank_context.sql`](../sql/04_create_world_bank_context.sql)
2. [`sql/05_create_enriched_view.sql`](../sql/05_create_enriched_view.sql)
3. [`sql/06_create_reporting_objects.sql`](../sql/06_create_reporting_objects.sql)
4. [`sql/09_create_jhu_extension.sql`](../sql/09_create_jhu_extension.sql)
5. [`sql/07_analysis_queries.sql`](../sql/07_analysis_queries.sql)

This order keeps WDI publication separate from the COVID denominator. It also creates the ECDC-only marts before the extended marts.

## 9. Start the local services

Start Docker before this step. Then run:

```bash
docker compose config
docker compose up -d --build
docker compose ps
```

Create the MongoDB collection and indexes:

```bash
docker compose exec api python -m scripts.setup_mongodb
```

## 10. Check the application

Run these requests:

```bash
curl -i http://localhost:8000/health/live
curl -i http://localhost:8000/health/ready
curl -i http://localhost:8000/health/snowflake
curl -i http://localhost:8000/countries
curl -i http://localhost:8000/dashboard/overview
```

Open <http://localhost:8050/overview>. Confirm that the Overview page contains data.

## Completion checklist

- [ ] Snowflake uses AWS Stockholm.
- [ ] `ECDC_GLOBAL` returns data.
- [ ] `JHU_COVID_19_TIMESERIES` returns data.
- [ ] `COVID_WH` and `COVID_PROJECT_MONITOR` exist.
- [ ] `STAGING.COVID_COUNTRY_DAILY` contains rows.
- [ ] `RAW.WORLD_BANK_INDICATOR_SNAPSHOTS` has one active snapshot.
- [ ] `MARTS.COUNTRY_COVID_DENOMINATOR` contains frozen population rows.
- [ ] `MARTS.COUNTRY_BASELINE_2019` contains eligible countries.
- [ ] `MARTS.COVID_ENRICHED` contains rows.
- [ ] `STAGING.COVID_COUNTRY_DAILY_EXTENDED` contains 222 locations.
- [ ] `MARTS.COUNTRY_LATEST_METRICS_EXTENDED` contains 222 rows.
- [ ] All four local services are healthy.
- [ ] The liveness, readiness, and Snowflake checks succeed.
- [ ] Two equal Overview requests return `MISS` and then `HIT`.
