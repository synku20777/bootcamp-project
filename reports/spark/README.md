# Spark optimization and clustering evidence

`evidence.json` remains the authoritative, machine-readable Spark artifact committed to Git. It is intentionally still evidence version 3, generated on 30 July 2026 from immutable source batch `wdi-context-qa-v1` by ingestion `bronze-wdi-v4` and benchmark `benchmark-wdi-v4`.

That accepted batch contains four checksum-verified files: 61,900 ECDC daily rows, 217 frozen 2020 population rows, 14 explicit country mappings, and 3,255 version-selected World Development Indicators observations. The WDI source is snapshot `wdi2-2019-2021-372906f371e0391f`.

Version 3 proves cross-engine context equivalence. Spark retained all 213 Snowflake-eligible countries, including countries with null WDI observations, and reproduced the 20,199-byte canonical projection with SHA-256 `6fa8fc8208d748a8dfbd2b4d606eb09cf2faae59869dfa52c62f3b3913872d83`. Its optimized physical plan contains three build-right broadcast hash joins: country mapping, frozen population, and the narrow WDI country baseline.

Every benchmark variant passed schema, row-count, and row-multiset checksum correctness gates before timing. Each comparison used one warm-up and five measured repetitions in one JVM.

| Comparison | Baseline median | Candidate median | Measured result |
| --- | ---: | ---: | --- |
| Early projection/filter | 183.939 ms | 216.075 ms | Candidate was not faster |
| Three broadcast joins | 762.517 ms | 561.944 ms | Candidate was faster |
| Adaptive duplicate aggregation | 347.056 ms | 426.586 ms | Candidate was not faster |
| Reused-frame cache | 323.322 ms | 372.224 ms | Candidate was not faster |
| File layout | 2,787.065 ms | 1,645.820 ms | Candidate was faster |

The quality result is `WARN`, not `FAIL`: 339 source rows have no source ISO value and the source contains 18 negative case corrections and 8 negative death corrections. These conditions are preserved and classified by policy. All fail-severity gates passed, and curated publication succeeded.

The version 3 calibration measured 1,891,286 bytes before final publication and used a proportional month-size estimate. It selected one unpartitioned 421,412-byte Parquet file because the estimated month directories were far below the 128 MiB target. That evidence remains historically valid for the implementation that produced it.

## Implemented version 4 contract

The code now requires source manifest version 3 and a fifth checksum-verified file exported from `COVID_ANALYTICS.MARTS.COVID_ENRICHED_EXTENDED`. Its manifest entry records row count, date range, country count, byte count, and checksum. The original ECDC file remains the fact input for transformation and benchmark comparisons; the extended governed mart is used only for clustering, so Spark does not duplicate Snowflake's splice logic.

Runtime policy is centralized and records both calculated values and overrides. Shuffle partitions are derived from source bytes and advisory partition bytes within bounded limits. Mapping, population, and WDI dimensions are explicitly broadcast only below the manifest-size threshold; otherwise Spark uses its non-broadcast plan. Automatic broadcast remains disabled during benchmarks so plan claims are deterministic.

Country-key skew evidence now includes key count, row count, median, p95, maximum, maximum-to-median ratio, and maximum row share. A ratio above 5 or share above 10% is a warning, not an automatic salting trigger. Event-log summaries now add memory spill, disk spill, peak execution memory, executor runtime, and JVM garbage-collection time. Bronze file counts use source bytes and a 128 MiB target, and month-layout calibration writes and measures actual compressed month directories rather than estimating them proportionally.

## Offline clustering contract

The analytical unit is canonical ISO3. Eligibility requires a positive governed population denominator, complete normalized measures, and at least 180 distinct daily observations. Exclusions are preserved by reason. Negative corrections remain in source data; incident rates are floored at zero only in the clustering working copy before complete 14-day rolling windows are calculated.

KMeans uses five COVID-only features:

- Latest cumulative cases per 100,000.
- Latest cumulative deaths per 100,000.
- Peak 14-day mean cases per 100,000.
- Peak 14-day mean deaths per 100,000.
- Volatility of the 14-day mean case rate.

Every feature is transformed with `log1p` and standardized. WDI values are excluded from the model vector and join only after fitting to create descriptive local profiles.

Candidate values `k=2..6` are evaluated over seeds 13, 29, 47, 71, and 97. A run is rejected if any cluster is smaller than `max(3, 2% of eligible countries)`. A candidate needs at least four valid seeds, positive median silhouette, and median pairwise Adjusted Rand Index of at least 0.75. Highest median silhouette wins, with smaller `k` selected when scores are within 0.01. The authoritative seed is closest to the median silhouette and lower seed breaks a tie. Arbitrary KMeans labels are replaced by IDs ordered on ascending standardized centroid burden.

Ignored immutable run output contains assignments, raw/log/standardized features, centroid distances, exclusions, WDI profiles, the scaler, and selected KMeans model. Publishable diagnostics contain only aggregate feature, eligibility, candidate, selection, cluster-size, checksum, runtime, skew, task-metric, and lineage evidence. They contain no country assignments, daily rows, or WDI cluster profiles.

## Evidence publication state

Fixture batches write `evidence.preview.json` and `clustering_diagnostics.preview.json` only under ignored run output. They cannot replace files in `reports/spark/`. Quality, correctness, stability, or publication failures occur before authoritative evidence replacement and therefore preserve the last accepted documents.

The offline clustering implementation and hardening are fixture-validated in the pinned Python 3.12.13, PySpark 3.5.6, and Java 17.0.19 runtime. No Snowflake credentials or immutable extended source batch were available for this change. Therefore:

- `evidence.json` remains version 3.
- No committed clustering diagnostics claim real-data results.
- Clustering is implemented, not yet demonstrated against the authoritative extended mart.
- Version 4 may be published only after a credentialed five-file export passes checksum, quality, benchmark, model-selection, stability, and atomic-publication gates.

Raw source extracts, Bronze and curated Parquet, Spark event logs, full plans, clustering assignments, cluster profiles, saved models, and benchmark scratch output remain local and excluded from Git. These local-mode techniques do not show that Spark is cost-effective for the approximately 61,900-row legacy workload or prove distributed scalability. Snowflake remains the production semantic and serving path.
