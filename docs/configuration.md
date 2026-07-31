# Configuration

Setup creates `.env` from `.env.example`. The API reads this file when it starts outside Compose.

Compose supplies internal service addresses. The dashboard reads only its container environment.

## Snowflake

| Variable | Default or required value | Purpose |
| --- | --- | --- |
| `SNOWFLAKE_ACCOUNT` | Required | Connector identifier in `organization-account` form |
| `SNOWFLAKE_USER` | Required | Snowflake login name |
| `SNOWFLAKE_PASSWORD` | Required | Snowflake login password |
| `SNOWFLAKE_BOOTSTRAP_ROLE` | `ACCOUNTADMIN` | Create account-level objects during setup |
| `SNOWFLAKE_ROLE` | `COVID_PROJECT_ADMIN` | Deploy project objects |
| `SNOWFLAKE_API_ROLE` | `COVID_APP_ROLE` | Read runtime marts |
| `SNOWFLAKE_WAREHOUSE` | `COVID_WH` | Run project queries |
| `SNOWFLAKE_DATABASE` | `COVID_ANALYTICS` | Store project objects |
| `SNOWFLAKE_SCHEMA` | `RAW` | Set the setup schema |
| `SNOWFLAKE_API_SCHEMA` | `MARTS` | Set the API schema |

Use `organization-account` for `SNOWFLAKE_ACCOUNT`. Do not use a URL or hostname.

Keep the three Snowflake roles separate. Do not use `ACCOUNTADMIN` as the API role.

## Dataset selection

| Variable | Default | Purpose |
| --- | --- | --- |
| `COVID_DATASET` | `extended` | Select the promoted ECDC and JHU marts |
| `CACHE_NAMESPACE` | `covid-api:v4` | Separate cached response contracts |

The normal settings are:

```dotenv
COVID_DATASET=extended
CACHE_NAMESPACE=covid-api:v4
```

Set `COVID_DATASET=legacy` to read the original ECDC-only marts. This setting does not replace or delete data.

Change `CACHE_NAMESPACE` when response semantics change. Clear the old project cache after a mart refresh.

## MongoDB and Redis

| Variable | Default or purpose |
| --- | --- |
| `MONGO_ROOT_USERNAME` | Root user for the MongoDB container |
| `MONGO_ROOT_PASSWORD` | Root password for the MongoDB container |
| `MONGO_DATABASE` | `covid_app` |
| `MONGODB_URI` | `mongodb://localhost:27017/covid_app` outside Compose |
| `REDIS_URL` | `redis://localhost:6379/0` outside Compose |

If `MONGO_ROOT_PASSWORD` is blank, setup creates a strong password. Setup also builds the encoded MongoDB URI.

Redis is required for analytical routes. The API does not query Snowflake when Redis is unavailable.

## Cache settings

Stable analytical responses use a 24-hour lifetime. Forecast responses use a six-hour lifetime.

The cache lock lasts 30 seconds. A waiting request can wait up to 10 seconds for another cache fill.

Context and comparison keys include the active WDI snapshot identifier. This rule prevents reuse across snapshot versions.

Clear only the project cache prefix:

```bash
docker compose exec api python -m scripts.clear_cache
```

The command uses Redis `SCAN`. It does not flush unrelated Redis data.

## Migration and rollback

Run the migration reconciliation before a consumer cutover:

```bash
uv run python scripts/reconcile_world_bank_migration.py
```

The reconciliation requires equal case, death, and denominator values. It uses small documented tolerances for calculated rates.

Use [`sql/08_migrate_population_compatibility.sql`](../sql/08_migrate_population_compatibility.sql) only after one verified release.

For an application rollback, set `COVID_DATASET=legacy`. Select a new cache namespace, and restart the API.

This rollback changes reads only. It does not rename, replace, or delete either mart family.

## Dashboard addresses

| Variable | Compose value | Purpose |
| --- | --- | --- |
| `DASHBOARD_API_BASE_URL` | `http://api:8000` | Connect from the dashboard container to the API |
| `DASHBOARD_PUBLIC_API_BASE_URL` | `http://localhost:8000` | Build links for the host browser |

Do not set the container API address to `localhost`. Inside the dashboard container, `localhost` identifies that container.
