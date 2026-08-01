# Spark workflow

Spark does not serve API requests. Snowflake SQL serves the current interactive workload.

The Spark workflow shows explicit schemas, immutable Bronze data, quality gates, plan inspection, measured file-layout decisions, and deterministic offline country clustering.

The Spark image uses Python 3.12.13, PySpark 3.5.6, Java 17.0.19, and `local[2]`.

## 1. Export a source batch

Complete the Snowflake setup first. Configure `.env` for `COVID_PROJECT_ADMIN`.

Run:

```bash
uv run python scripts/export_spark_sources.py \
  --source-batch-id wdi-context-v1
```

This is the only Spark step that contacts Snowflake. It writes one immutable batch under `data/source/`.

The source contract contains five files:

- ECDC daily data.
- The governed `COVID_ANALYTICS.MARTS.COVID_ENRICHED_EXTENDED` series.
- The frozen 2020 population denominator.
- Explicit country mappings.
- Active WDI observations.

The manifest records row counts, byte counts, checksums, the WDI snapshot identifier, and the active Snowflake publication generation. The exporter reads the generation before and after all five queries. It rejects the batch and removes its staging directory if the generation is absent or changes. The extended entry also records the selected Snowflake object, date range, country count, and SHA-256 checksum.

The exporter does not replace an existing batch identifier.

## 2. Run Bronze ingestion and profiling

Build the Spark image:

```bash
docker compose --profile spark build spark
```

Run ingestion and profiling:

```bash
docker compose --profile spark run --rm spark ingest-profile \
  --source-batch-id wdi-context-v1 \
  --ingestion-id bronze-v1 \
  --benchmark-run-id benchmark-v1
```

The job checks exact headers before parsing. It applies explicit Spark schemas to all sources.

The job writes input and corrupt records to immutable Bronze Parquet. Quality failures block curated publication.

| Condition | Result |
| --- | --- |
| Schema drift or corrupt input | Failure |
| Missing required identity or date | Failure |
| Invalid ISO length | Failure |
| Duplicate mapping or population key | Failure |
| Duplicate extended country-date or inconsistent source segment | Failure |
| Missing or non-positive clustering denominator | Warning and country exclusion |
| Null clustering measure or fewer than 180 observations | Warning and country exclusion |
| Null daily measure | Warning |
| Duplicate normalized country-date | Warning |
| Recoverable missing ISO code | Warning |
| Negative case or death correction | Information |

Schema failures publish only quality evidence. Other failures can publish Bronze and quarantine evidence.

Warnings allow curated publication. The Bronze manifest records the ruleset, quality-document checksum, source kind, runtime policy, and calculated and actual file counts.

Shuffle partitions derive from total input bytes and a configurable advisory size. A dimension receives a broadcast hint only when its manifest byte count is below the configured limit. The recorded physical plan must agree with these decisions. Skew evidence records the median, p95, maximum, maximum share, and maximum-to-median ratio by country key. Event-log evidence records shuffle and input bytes, spill, peak execution memory, executor runtime, and JVM garbage-collection time.

## 3. Build offline clusters

ISO3 is the analytical unit. Eligible countries need a positive population denominator, complete normalized measures, and at least 180 daily observations.

The working copy floors negative incident corrections at zero before it calculates complete 14-day rolling means. Bronze retains the original values.

The model uses these five COVID-only features:

- Latest cumulative cases per 100,000.
- Latest cumulative deaths per 100,000.
- Peak 14-day mean cases per 100,000.
- Peak 14-day mean deaths per 100,000.
- Volatility of the 14-day mean case rate.

The pipeline applies `log1p` and standardization before Spark ML KMeans. WDI variables join only after fitting and cannot affect cluster membership.

Model selection evaluates `k=2..6` across five fixed seeds. It rejects small clusters.

Publication requires four valid seeds, a positive median silhouette, and a median pairwise Adjusted Rand Index of at least 0.75. The selected run writes all artifacts atomically.

The artifacts contain assignments, features, distances, exclusions, descriptive profiles, and fitted models. Only aggregate diagnostics can enter the repository.

## 4. Run another benchmark

Run:

```bash
docker compose --profile spark run --rm spark benchmark \
  --ingestion-id bronze-v1 \
  --benchmark-run-id benchmark-v2
```

Each comparison uses one warmup and five measured repetitions. The benchmark alternates the variant order.

Both variants must pass the correctness gate before timing. The gate checks schema, row count, and a row-multiset checksum.

A failed gate stops timing and keeps the previous evidence. Fixture runs create ignored preview evidence only. A checksum-verified Snowflake export can replace `reports/spark/evidence.json` only after the quality, correctness, physical-plan, and clustering gates pass.

The benchmark measures these decisions:

- Early projection and filtering.
- Broadcast joins for small lookup data.
- Adaptive query execution.
- Persistence for a reused data frame.
- Output partition and file count.

The measured fixture does not prove that every common optimization is faster. The evidence separates plan changes from timing changes.

## Current evidence

Evidence version 4 uses source batch `extended-context-20260731-v1`, ingestion `bronze-wdi-v5`, and benchmark/model `benchmark-wdi-v5`. The source manifest, evidence, and clustering diagnostics all record Snowflake generation `548e6113-ef7a-4243-8f4b-e79aab0e6e5b`.

All five file checksums passed. Spark reproduced the 213-row Snowflake context projection with SHA-256 `6fa8fc8208d748a8dfbd2b4d606eb09cf2faae59869dfa52c62f3b3913872d83`, and the optimized plan recorded three build-right broadcast hash joins.

Five-repetition medians support broadcast joins and one unpartitioned output file on this local workload. Early projection/filtering, AQE duplicate aggregation, and reused-frame caching were slower. Skew passed without salting. The selected `k=2` cluster result includes 211 countries, has median silhouette 0.7350874333726615, and has median pairwise Adjusted Rand Index 0.8356107111065171.

See the [Spark evidence report](../reports/spark/README.md) for fingerprints, benchmark medians, and file-layout results.

## 5. Run the Spark tests

Install the Spark dependency group:

```bash
uv sync --locked --group spark
```

Run the tests:

```bash
uv run --group spark python -m unittest discover -s spark_tests -v
```
