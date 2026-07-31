# Spark optimization and clustering evidence

`evidence.json` remains the authoritative, machine-readable Spark artifact committed to Git. It is intentionally still evidence version 3, generated on 30 July 2026 from immutable source batch `wdi-context-qa-v1` by ingestion `bronze-wdi-v4` and benchmark `benchmark-wdi-v4`.

That accepted batch contains four checksum-verified files:

- 61,900 ECDC daily rows.
- 217 frozen 2020 population rows.
- 14 explicit country mappings.
- 3,255 version-selected World Development Indicators observations.

The WDI source is snapshot `wdi2-2019-2021-372906f371e0391f`.

Version 3 proves cross-engine context equivalence. Spark retained all 213 Snowflake-eligible countries, including countries with null WDI observations, and reproduced the 20,199-byte canonical projection with SHA-256 `6fa8fc8208d748a8dfbd2b4d606eb09cf2faae59869dfa52c62f3b3913872d83`. Its optimized physical plan contains three build-right broadcast hash joins: country mapping, frozen population, and the narrow WDI country baseline.

Every benchmark variant passed schema, row-count, and row-multiset checksum gates before timing. Each comparison used one warmup and five measured repetitions in one JVM.

| Comparison | Baseline median | Candidate median | Measured result |
| --- | ---: | ---: | --- |
| Early projection/filter | 183.939 ms | 216.075 ms | Candidate was not faster |
| Three broadcast joins | 762.517 ms | 561.944 ms | Candidate was faster |
| Adaptive duplicate aggregation | 347.056 ms | 426.586 ms | Candidate was not faster |
| Reused-frame cache | 323.322 ms | 372.224 ms | Candidate was not faster |
| File layout | 2,787.065 ms | 1,645.820 ms | Candidate was faster |

The quality result is `WARN`, not `FAIL`. A total of 339 source rows have no source ISO value.

The source also contains 18 negative case corrections and 8 negative death corrections. The pipeline preserves these conditions and classifies them by policy.

All fail-severity gates passed, and curated publication succeeded.

The version 3 calibration measured 1,891,286 bytes before final publication and used a proportional month-size estimate. It selected one unpartitioned 421,412-byte Parquet file because the estimated month directories were far below the 128 MiB target. That evidence remains historically valid for the implementation that produced it.

## Implemented version 4 contract

The code now requires source manifest version 3. It also requires a fifth checksum-verified file from `COVID_ANALYTICS.MARTS.COVID_ENRICHED_EXTENDED`.

Its manifest entry records row count, date range, country count, byte count, and checksum. The original ECDC file remains the transformation and benchmark fact input.

Spark uses the governed extended mart only for clustering. Spark does not duplicate the Snowflake splice logic.

One runtime policy records calculated values and overrides. The policy derives bounded shuffle partitions from source bytes and the advisory partition size.

Spark broadcasts mapping, population, and WDI dimensions only below the manifest-size threshold. Otherwise, Spark uses its non-broadcast plan.

Benchmarks disable automatic broadcast so physical-plan claims remain deterministic.

Country-key skew evidence includes key count, row count, median, p95, maximum, maximum-to-median ratio, and maximum row share. A ratio above 5 or share above 10% creates a warning.

The warning does not add salting automatically. Event-log summaries include spill, peak execution memory, executor runtime, and JVM garbage-collection time.

Bronze file counts use source bytes and a 128 MiB target. Month-layout calibration writes and measures compressed month directories instead of estimating them.

## Offline clustering contract

The analytical unit is canonical ISO3. Eligibility requires a positive governed population denominator, complete normalized measures, and at least 180 distinct daily observations.

The evidence retains each exclusion reason. Source data retains negative corrections.

The clustering working copy floors incident rates at zero before it calculates complete 14-day rolling windows.

KMeans uses five COVID-only features:

- Latest cumulative cases per 100,000.
- Latest cumulative deaths per 100,000.
- Peak 14-day mean cases per 100,000.
- Peak 14-day mean deaths per 100,000.
- Volatility of the 14-day mean case rate.

The pipeline applies `log1p` and standardization to each feature. WDI values do not enter the model vector.

WDI values join only after fitting to create descriptive local profiles.

The selector evaluates `k=2..6` over seeds 13, 29, 47, 71, and 97. It rejects a run when any cluster is below `max(3, 2% of eligible countries)`.

A candidate needs four valid seeds, a positive median silhouette, and a median pairwise Adjusted Rand Index of at least 0.75. The highest median silhouette wins.

When scores differ by 0.01 or less, the selector uses the smaller `k`. The authoritative seed is closest to the median silhouette.

A lower seed breaks a tie. The pipeline replaces arbitrary KMeans labels with IDs ordered by ascending standardized centroid burden.

Ignored immutable run output contains assignments, raw/log/standardized features, centroid distances, exclusions, WDI profiles, the scaler, and selected KMeans model. Publishable diagnostics contain only aggregate feature, eligibility, candidate, selection, cluster-size, checksum, runtime, skew, task-metric, and lineage evidence. They contain no country assignments, daily rows, or WDI cluster profiles.

## Evidence publication state

Fixture batches write `evidence.preview.json` and `clustering_diagnostics.preview.json` only under ignored run output. They cannot replace files in `reports/spark/`. Quality, correctness, stability, or publication failures occur before authoritative evidence replacement and therefore preserve the last accepted documents.

Fixture data validates the offline clustering implementation in the pinned runtime. The runtime uses Python 3.12.13, PySpark 3.5.6, and Java 17.0.19.

This change had no Snowflake credentials or immutable extended source batch. Therefore:

- `evidence.json` remains version 3.
- No committed clustering diagnostics claim real-data results.
- The project implements clustering but does not claim authoritative extended-mart results.
- Publish version 4 only after a credentialed five-file export passes all quality and publication gates.

The repository excludes raw extracts, Parquet data, event logs, full plans, model artifacts, and benchmark scratch output. These artifacts remain local.

Local-mode results do not prove Spark cost efficiency for the 61,900-row legacy workload. They also do not prove distributed scalability.

Snowflake remains the production semantic and serving path.
