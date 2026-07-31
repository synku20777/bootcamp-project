# COVID-19 Analytics Platform

This bootcamp project runs a COVID-19 data platform with Snowflake, FastAPI, Dash, MongoDB, Redis, and PySpark.

The automatic setup creates the Snowflake objects and starts the local services. The normal workflow requires **Docker only**.

## Start here: run the project from a new computer

Run these steps from the repository root. Run the steps in the given order.

You do not need to install Python, uv, Java, MongoDB, Redis, or Snowflake command-line tools.

Setup builds a private Python/uv environment inside Docker. The application then runs in four local containers.

### What the project starts

| System | Address | Purpose |
| --- | --- | --- |
| Dash | <http://localhost:8050/overview> | Show the interactive dashboard |
| FastAPI | <http://localhost:8000/docs> | Serve the API and Swagger UI |
| MongoDB | `127.0.0.1:27017` | Store user annotations |
| Redis | Docker network only | Cache API responses and protect the query budget |
| Snowflake | Remote trial account | Store Marketplace data and analytical marts |

Redis is a required dependency for analytical routes. The API does not query Snowflake when Redis is unavailable.

### 1. Create the Snowflake account

1. Open the [Snowflake trial registration page](https://signup.snowflake.com/).
2. Select **Amazon Web Services (AWS)**.
3. Select **Europe (Stockholm)**.
4. Create the account.
5. Sign in to Snowsight.
6. Save the organization name, account name, username, and password in a password manager.

Use `organization-account` for `SNOWFLAKE_ACCOUNT`. Do not use a URL or a `snowflakecomputing.com` hostname.

### 2. Add the Marketplace database

1. Open **Data Products** or **Marketplace** in Snowsight.
2. Find the free **COVID-19 Epidemiological Data** listing.
3. Add the listing to the current account.
4. Name the database `COVID19_EPIDEMIOLOGICAL_DATA`.
5. Open a Snowsight worksheet.
6. Run these queries:

```sql
SELECT *
FROM COVID19_EPIDEMIOLOGICAL_DATA.PUBLIC.ECDC_GLOBAL
LIMIT 1;

SELECT *
FROM COVID19_EPIDEMIOLOGICAL_DATA.PUBLIC.JHU_COVID_19_TIMESERIES
LIMIT 1;
```

Both queries must return a row. A different database name does not meet the project contract.

### 3. Install Docker

On Windows or macOS, install [Docker Desktop](https://www.docker.com/products/docker-desktop/).

On Linux, install Docker Engine and the Compose v2 plugin from the [Docker documentation](https://docs.docker.com/engine/install/).

Start the Docker engine. Then run these checks:

```bash
docker --version
docker info
docker compose version
```

`docker info` must show a **Server** section. If Linux reports a permission error, configure non-root Docker access.

Git is optional. Use Git for updates, or download a ZIP file from GitHub.

### 4. Get the repository

With Git, run:

```bash
git clone <repository-url>
cd bootcamp-project
```

Without Git, download the repository ZIP. Extract it and open the `bootcamp-project` folder in a terminal.

On Windows, verify the current folder:

```powershell
Get-Location
Get-Item README.md, compose.yaml, setup.ps1, setup.sh
```

On macOS or Linux, verify the current folder:

```bash
pwd
ls README.md compose.yaml setup.ps1 setup.sh
```

If a required file is missing, change to the repository root.

### 5. Prepare the configuration

Setup creates `.env` from `.env.example`. It prompts only for missing account values and secrets.

Setup backs up an existing `.env` file before replacement. Git and Docker ignore this file.

Do not commit `.env`. Do not paste its contents into an issue or log.

Prepare these Snowflake values:

| Variable | Required value |
| --- | --- |
| `SNOWFLAKE_ACCOUNT` | Connector identifier in `organization-account` form |
| `SNOWFLAKE_USER` | Snowflake login name |
| `SNOWFLAKE_PASSWORD` | Snowflake login password |
| `SNOWFLAKE_BOOTSTRAP_ROLE` | `ACCOUNTADMIN` |
| `SNOWFLAKE_ROLE` | `COVID_PROJECT_ADMIN` |
| `SNOWFLAKE_API_ROLE` | `COVID_APP_ROLE` |

Keep the three roles separate. Do not use `ACCOUNTADMIN` as the API role.

Setup supplies the remaining project defaults. See [Configuration](docs/configuration.md) for the complete reference.

### 6. Run the one-time setup

Before this step, make sure that Docker is running.

On Windows PowerShell, run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
```

The execution-policy change applies only to the current PowerShell process.

On macOS or Linux, run:

```bash
chmod +x setup.sh start.sh stop.sh
./setup.sh
```

Setup completes these operations:

1. It checks Docker, Compose, file permissions, and required ports.
2. It creates or validates `.env` without printing secrets.
3. It checks both required Marketplace tables.
4. It creates the Snowflake warehouse, database, schemas, and roles.
5. It publishes the committed World Bank snapshot.
6. It preserves the frozen COVID population denominator.
7. It creates the staging views, serving tables, analytical marts, and reporting objects.
8. It starts FastAPI, Dash, MongoDB, and Redis.
9. It creates MongoDB indexes and runs application checks.

Setup writes a redacted JSONL audit log under `outputs/setup/`.

If setup stops, correct the reported cause. Then run the resume command.

On Windows, run:

```powershell
.\setup.ps1 --resume
```

On macOS or Linux, run:

```bash
./setup.sh --resume
```

For unattended setup, prepare a complete `.env` file. Then run:

```powershell
.\setup.ps1 --resume --non-interactive
```

```bash
./setup.sh --resume --non-interactive
```

### 7. Verify the setup

Run:

```bash
docker compose ps
```

The `api`, `dashboard`, `mongo`, and `redis` services must be healthy.

On Windows PowerShell, run:

```powershell
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
Invoke-RestMethod http://localhost:8000/countries
```

On macOS or Linux, run:

```bash
curl -i http://localhost:8000/health/live
curl -i http://localhost:8000/health/ready
curl -i http://localhost:8000/countries
```

The health routes must return HTTP `200`. The countries route must return a non-empty JSON list.

Run the Snowflake check only when you need a live warehouse test:

```bash
curl -i http://localhost:8000/health/snowflake
```

This request can resume `COVID_WH` and use Snowflake credits.

### 8. Use the dashboard

Open <http://localhost:8050/overview>.

Use the Status page to check each service. Use the Patterns page to review sustained daily case increases.

Use the other pages to review countries, comparisons, forecasts, and annotations.

The dashboard does not check Snowflake during page load. Select **Check Snowflake** when you need that test.

### 9. Start and stop the project

After the first setup, use the start script for daily work.

On Windows, run:

```powershell
.\start.ps1
```

On macOS or Linux, run:

```bash
./start.sh
```

Use the stop script to stop containers without deleting data.

On Windows, run:

```powershell
.\stop.ps1
```

On macOS or Linux, run:

```bash
./stop.sh
```

`setup` creates the environment. `start` starts existing services. `stop` preserves MongoDB, Redis, and Snowflake data.

### 10. Quick recovery

- If Docker is unavailable, start the engine and run `docker info`.
- If setup stops, correct the reported cause and run setup with `--resume`.
- If an API route returns `503`, read `error.code` and `request_id`.
- If Redis is unavailable, restore Redis. Do not bypass the cache.
- If the dashboard cannot reach the API, check the `api` service.

See [Troubleshooting](docs/troubleshooting.md) for complete recovery instructions.

## Architecture

See [Project overview](docs/project-overview.md) and [Data sources](docs/data-sources.md).

## Advanced: manual setup and recovery

See [Manual setup](docs/manual-setup.md), including the `sql/09_create_jhu_extension.sql` step.

## More documentation

| Document | Purpose |
| --- | --- |
| [Project overview](docs/project-overview.md) | Review capabilities, architecture, and project structure |
| [Data sources](docs/data-sources.md) | Review source selection and data boundaries |
| [Manual setup](docs/manual-setup.md) | Run each deployment step manually |
| [API reference](docs/api-reference.md) | Review endpoints, caching, and annotations |
| [Configuration](docs/configuration.md) | Review environment variables and runtime choices |
| [Analytics](docs/analytics.md) | Review patterns, forecasting, and EDA |
| [Spark](docs/spark.md) | Run ingestion, quality checks, benchmarks, and offline clustering |
| [Development](docs/development.md) | Run local development and code checks |
| [Troubleshooting](docs/troubleshooting.md) | Correct setup and runtime problems |
| [World Bank country context](docs/architecture/world-bank-context.md) | Review the WDI architecture and migration policy |
