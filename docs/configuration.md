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
| `SNOWFLAKE_LOGIN_TIMEOUT_SECONDS` | `10` | Bound login and authentication attempts |
| `SNOWFLAKE_NETWORK_TIMEOUT_SECONDS` | `30` | Bound connector network operations |
| `SNOWFLAKE_STATEMENT_TIMEOUT_SECONDS` | `30` | Bound each API statement in Snowflake |
| `SNOWFLAKE_QUERY_TAG_PREFIX` | `covid-api` | Identify API statements in Query History |
| `SNOWFLAKE_USE_CACHED_RESULT` | `true` | Allow Snowflake result-cache reuse for normal serving |

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

Every COVID-derived cache key also includes `COVID_DATASET`. A legacy response and an extended response therefore cannot share a cache entry. Change `CACHE_NAMESPACE` when response semantics change.

API query tags use `<prefix>:<dataset>:<operation>`. This format separates legacy and extended traffic. It also attributes each statement to one repository operation.

The live profiler disables `SNOWFLAKE_USE_CACHED_RESULT`. Normal API serving keeps it enabled.

## MongoDB and Redis

| Variable | Default or purpose |
| --- | --- |
| `MONGO_ROOT_USERNAME` | Root user for the MongoDB container |
| `MONGO_ROOT_PASSWORD` | Root password for the MongoDB container |
| `MONGO_DATABASE` | `covid_app` |
| `MONGODB_URI` | `mongodb://localhost:27017/covid_app` outside Compose |
| `REDIS_URL` | `redis://localhost:6379/0` outside Compose |

If `MONGO_ROOT_PASSWORD` is blank, setup creates a strong password. Setup also builds the encoded MongoDB URI.

Analytical routes require Redis. The API does not query Snowflake when Redis is unavailable.

## Cache settings

Stable analytical responses use a 24-hour lifetime. Forecast responses use a six-hour lifetime.

The cache lock lasts 60 seconds. A waiting request can wait up to 15 seconds for another cache fill. The lease covers the 30-second Snowflake statement bound, while the shorter wait prevents a request from waiting for the complete lease.

Every COVID-derived key includes `COVID_DATASET`. Comparison and combined-page keys also include the committed WDI snapshot identifier. WDI-only context keys remain snapshot-based.

If the application cannot read the WDI manifest, an optional combined page uses the explicit revision `unavailable`. This identity permits a cached COVID-only response.

The response does not share a key with verified WDI context. The context-only route still fails closed.

After a mart refresh, first validate the new marts and rebuild `COUNTRY_LATEST_METRICS_EXTENDED`. Clear the project cache only after publication succeeds. This order preserves the last-known-good cache if publication fails.

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
