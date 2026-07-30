# Spark workflow

Spark does not serve API requests. Snowflake SQL serves the current interactive workload.

The Spark workflow shows explicit schemas, immutable Bronze data, quality gates, plan inspection, and measured file-layout decisions.

The Spark image uses Python 3.12.13, PySpark 3.5.6, Java 17.0.19, and `local[2]`.

## 1. Export a source batch

Complete the Snowflake setup first. Configure `.env` for `COVID_PROJECT_ADMIN`.

Run:

```bash
uv run python scripts/export_spark_sources.py \
  --source-batch-id wdi-context-v1
```

This is the only Spark step that contacts Snowflake. It writes one immutable batch under `data/source/`.

The batch contains four files:

- ECDC daily data.
- The frozen 2020 population denominator.
- Explicit country mappings.
- Active WDI observations.

The manifest records row counts, byte counts, checksums, and the WDI snapshot identifier.

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
| Non-positive population | Failure |
| Null daily measure | Warning |
| Duplicate normalized country-date | Warning |
| Recoverable missing ISO code | Warning |
| Negative case or death correction | Information |

Schema failures publish only quality evidence. Other failures can publish Bronze and quarantine evidence.

Warnings allow curated publication. The Bronze manifest records the ruleset and quality-document checksum.

## 3. Run another benchmark

Run:

```bash
docker compose --profile spark run --rm spark benchmark \
  --ingestion-id bronze-v1 \
  --benchmark-run-id benchmark-v2
```

Each comparison uses one warm-up and five measured repetitions. The benchmark alternates the variant order.

Both variants must pass the correctness gate before timing. The gate checks schema, row count, and a row-multiset checksum.

A failed gate stops timing and keeps the previous evidence. A successful run replaces `reports/spark/evidence.json` atomically.

The benchmark measures these decisions:

- Early projection and filtering.
- Broadcast joins for small lookup data.
- Adaptive query execution.
- Persistence for a reused data frame.
- Output partition and file count.

The measured fixture does not prove that every common optimization is faster. The evidence separates plan changes from timing changes.

## Current evidence

Evidence version 3 uses source batch `wdi-context-qa-v1` and snapshot `wdi2-2019-2021-372906f371e0391f`.

Spark reproduced the Snowflake context baseline with these values:

- 213 rows.
- 20,199 canonical bytes.
- SHA-256 `6fa8fc8208d748a8dfbd2b4d606eb09cf2faae59869dfa52c62f3b3913872d83`.

The optimized plan contains three build-right broadcast hash joins.

| Comparison | Baseline median | Candidate median | Result |
| --- | ---: | ---: | --- |
| Early projection and filter | 183.939 ms | 216.075 ms | Candidate was not faster |
| Three broadcast joins | 762.517 ms | 561.944 ms | Candidate was faster |
| Adaptive duplicate aggregation | 347.056 ms | 426.586 ms | Candidate was not faster |
| Reused-frame cache | 323.322 ms | 372.224 ms | Candidate was not faster |
| File layout | 2,787.065 ms | 1,645.820 ms | Candidate was faster |

The published Parquet size is 421,412 bytes. The pipeline writes one unpartitioned file at this size.

## 4. Run the Spark tests

Install the Spark dependency group:

```bash
uv sync --locked --group spark
```

Run the tests:

```bash
uv run --group spark python -m unittest discover -s spark_tests -v
```

