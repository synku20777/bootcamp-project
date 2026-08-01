# Spark optimization and clustering evidence

`evidence.json` is the authoritative machine-readable Spark artifact. Evidence version 4 was generated on 31 July 2026 from immutable Snowflake export `extended-context-20260731-v1`, ingestion `bronze-wdi-v5`, and benchmark/model `benchmark-wdi-v5` in the pinned Python 3.12.13, PySpark 3.5.6, Java 17.0.19 image using `local[2]`.

## Source and publication lineage

The exporter read publication generation `548e6113-ef7a-4243-8f4b-e79aab0e6e5b` before and after the export. The source manifest, evidence, and clustering diagnostics record that same generation. This brackets all five files to one validated Snowflake `MARTS` generation without adding generation columns to analytical rows.

The checksum-verified batch contains:

- 224,265 governed extended COVID rows covering 31 December 2019 through 9 March 2023 across 221 ISO3 countries.
- 61,900 ECDC daily rows.
- 217 frozen 2020 population rows.
- 14 explicit country mappings.
- 3,255 version-selected World Development Indicators observations.

The combined batch SHA-256 is `64b3fa095be168b8d8d9c255813c58864e32352e00322e2083cfbe43205bdf7a`. The WDI snapshot is `wdi2-2019-2021-372906f371e0391f`.

Spark retained all 213 Snowflake-eligible context countries, including countries with null WDI observations. It reproduced the 20,199-byte Snowflake projection with SHA-256 `6fa8fc8208d748a8dfbd2b4d606eb09cf2faae59869dfa52c62f3b3913872d83`.

## Measured optimization results

Every variant passed schema, row-count, and row-multiset checksum gates before timing. Each comparison used one warmup and five measured repetitions in one JVM.

| Comparison | Baseline median | Candidate median | Measured result |
| --- | ---: | ---: | --- |
| Early projection/filter | 282.918 ms | 325.457 ms | Candidate was not faster |
| Three broadcast joins | 1,698.491 ms | 1,150.329 ms | Candidate was faster |
| Adaptive duplicate aggregation | 589.043 ms | 1,245.694 ms | Candidate was not faster |
| Reused-frame cache | 395.312 ms | 426.186 ms | Candidate was not faster |
| File layout | 4,158.977 ms | 1,880.695 ms | Candidate was faster |

The optimized physical plan contains three build-right broadcast hash joins for country mapping, frozen population, and the narrow WDI country baseline. Each lookup was below the recorded 8 MiB threshold. Broadcast joins were faster despite higher measured shuffle bytes, so the conclusion is limited to elapsed time on this captured workload.

AQE remains enabled as a scale-safety control, not a speed claim. The cache candidate reduced input bytes but did not reduce median elapsed time. These negative results prevent default persistence or an AQE performance claim.

The 27,910,999 source bytes produced four bounded shuffle partitions. Country-key skew passed: the maximum-to-median ratio was 1.067828 and the largest key held 0.5195% of 224,265 rows. No salting is justified at this distribution.

Measured monthly Parquet directories had a 70,267-byte median. The pipeline therefore published one unpartitioned 421,784-byte Parquet file instead of thirteen tiny month partitions. The full cold run took 277,097.669 ms. Local mode and this data volume do not prove distributed cost efficiency.

## Quality and clustering result

Quality was `WARN`, not `FAIL`, and both Bronze and curated publication succeeded. The warnings preserve known source conditions: 339 ECDC rows lack a source ISO value, while the extended source contains 139 negative case corrections and 147 negative death corrections.

Clustering used five population-normalized COVID features. It applied `log1p` and standardization, evaluated `k=2..6` over seeds 13, 29, 47, 71, and 97, and joined WDI values only after fitting. WDI therefore describes clusters but cannot determine membership.

Of 222 locations, 211 were eligible. One lacked ISO3 and ten lacked a valid population denominator. The selected model uses `k=2`, seed 13, and ordered cluster sizes 87 and 124. Its selected silhouette is 0.735087, median silhouette is 0.7350874333726615, and median pairwise Adjusted Rand Index is 0.8356107111065171. These values pass the positive-silhouette, minimum-size, and 0.75 stability gates.

`clustering_diagnostics.json` contains aggregate eligibility, model-selection, stability, checksum, runtime, skew, and lineage evidence. It excludes country assignments, daily rows, and WDI profiles. Those detailed artifacts remain in ignored immutable local output.

Clusters describe historical reporting outcomes. They are not causal, policy, clinical, or epidemiological regimes.

## Publication policy and limitations

Fixture batches can write only ignored preview evidence. A Snowflake export can replace the committed evidence and clustering diagnostics only after source checksums, quality, benchmark correctness, physical-plan, context-equivalence, clustering, and curated-publication gates pass. Failed runs preserve the last accepted documents.

Raw extracts, Parquet data, event logs, full plans, models, assignments, and benchmark scratch output remain untracked. Snowflake remains the production semantic and serving path; Spark remains an offline verification, engineering, and clustering path.
