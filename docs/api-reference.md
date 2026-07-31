# API reference

Open Swagger UI at <http://localhost:8000/docs> after the API starts.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health`, `/health/live` | Check process liveness without dependency calls |
| `GET` | `/health/ready` | Check MongoDB and Redis |
| `GET` | `/health/snowflake` | Check Snowflake and required marts |
| `GET` | `/countries` | List canonical country identities |
| `GET` | `/countries/{identifier}/summary` | Get the latest country metrics |
| `GET` | `/countries/{identifier}/timeseries` | Get a filtered metric series |
| `GET` | `/countries/{identifier}/context` | Get versioned WDI context |
| `GET` | `/compare` | Compare one metric for two to ten countries |
| `GET` | `/forecast` | Get an evaluated daily forecast |
| `GET` | `/patterns/case-increases` | Explore sustained daily case-increase patterns |
| `GET` | `/dashboard/overview` | Get the complete Overview payload |
| `GET` | `/dashboard/countries/{identifier}` | Get the complete Country Explorer payload |
| `GET` | `/dashboard/compare` | Get the complete Comparison payload |
| `POST` | `/annotations` | Validate and create an annotation |
| `GET` | `/annotations` | Get filtered annotations |
| `GET` | `/docs` | Open Swagger UI |

The `/dashboard/compare` response includes an optional `world_bank_context` object for each country.

`world_bank_context_status` is `available` or `context_data_unavailable`. Missing WDI context does not remove COVID results.

`countries_without_data` lists countries with no COVID observations in the requested date range.

## Acceptance requests

Run these requests after setup:

```bash
curl -i http://localhost:8000/health/live
curl -i http://localhost:8000/health/ready
curl -i http://localhost:8000/health/snowflake
curl -i http://localhost:8000/dashboard/overview
curl -i http://localhost:8000/dashboard/overview
curl -i "http://localhost:8000/dashboard/countries/LV?metric=cases_per_100k&start_date=2020-03-01&end_date=2023-03-09"
curl -i "http://localhost:8000/dashboard/compare?country=LV&country=EE&start_date=2020-03-01&end_date=2023-03-09"
curl -i http://localhost:8000/countries/LV/summary
curl -i http://localhost:8000/countries/LV/context
curl -i "http://localhost:8000/forecast?country=LV&metric=new_cases&days=30&lookback_days=90"
curl -i "http://localhost:8000/patterns/case-increases?start_date=2020-03-01&end_date=2023-03-09&minimum_consecutive_increases=3&limit=100"
```

The first Overview response must include `X-Cache: MISS`. The second equal request must include `X-Cache: HIT`.

## Cache behavior

Stable analytical responses use a 24-hour lifetime. Forecast responses use a six-hour lifetime.

Every COVID-derived cache key includes `COVID_DATASET`. Comparison and combined-page keys also include the committed WDI snapshot identifier.

WDI-only context keys remain snapshot-based. A dataset cutover or WDI publication cannot reuse an ambiguous response.

If the application cannot read the manifest, an optional combined page uses the explicit context revision `unavailable`. The page can then show COVID-only content.

The context-only route still fails closed. A cache-fill lock lasts 60 seconds, and waiters stop after 15 seconds.

Page-level dashboard stores prevent one API request for each chart. Render callbacks use the stored page response.

See [Configuration](configuration.md) for cache namespace and connection settings.

## Annotations

Create or verify the MongoDB indexes:

```bash
docker compose exec api python -m scripts.setup_mongodb
```

Create an annotation:

```bash
curl -i -X POST http://localhost:8000/annotations \
  -H "Content-Type: application/json" \
  -d '{"country":"LV","report_date":"2020-03-15","metric":"new_cases","comment":"Reporting delay.","created_by":"Student"}'
```

Get annotations for a country:

```bash
curl -i "http://localhost:8000/annotations?country=LV&metric=new_cases&start_date=2020-03-01&end_date=2020-03-31"
```

The API validates the country and report date against Snowflake before it stores the annotation.

The API stores annotations in MongoDB. It clears the related Redis cache key after a successful write.
