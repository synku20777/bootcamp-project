# Spark optimization evidence

`evidence.json` is the authoritative, machine-readable Spark artifact committed
to Git. Evidence version 3 was generated on 30 July 2026 from immutable source
batch `wdi-context-qa-v1` by ingestion `bronze-wdi-v4` and benchmark
`benchmark-wdi-v4`.

The current source contract contains four checksum-verified files: 61,900 ECDC
daily rows, 217 frozen 2020 population rows, 14 explicit country mappings, and
3,255 version-selected World Development Indicators observations. The WDI
source is snapshot `wdi2-2019-2021-372906f371e0391f`.

Version 3 proves cross-engine context equivalence. Spark retained all 213
Snowflake-eligible countries, including countries with null WDI observations,
and reproduced the 20,199-byte canonical projection with SHA-256
`6fa8fc8208d748a8dfbd2b4d606eb09cf2faae59869dfa52c62f3b3913872d83`.
The optimized physical plan contains three build-right broadcast hash joins:
country mapping, frozen population, and the narrow WDI country baseline.

Every benchmark variant passed schema, row-count, and row-multiset checksum
correctness gates before timing. Each comparison used one warm-up and five
measured repetitions in one JVM.

| Comparison | Baseline median | Candidate median | Measured result |
| --- | ---: | ---: | --- |
| Early projection/filter | 183.939 ms | 216.075 ms | Candidate was not faster |
| Three broadcast joins | 762.517 ms | 561.944 ms | Candidate was faster |
| Adaptive duplicate aggregation | 347.056 ms | 426.586 ms | Candidate was not faster |
| Reused-frame cache | 323.322 ms | 372.224 ms | Candidate was not faster |
| File layout | 2,787.065 ms | 1,645.820 ms | Candidate was faster |

The quality result is `WARN`, not `FAIL`: 339 source rows have no source ISO
value and the source contains 18 negative case corrections and 8 negative death
corrections. These conditions are preserved and classified by policy. All fail
severity gates passed, and curated publication succeeded.

The calibrated output measured 1,891,286 bytes before final publication.
Thirteen hypothetical monthly partitions had a median size of 191,542 bytes,
far below the 128 MiB target, so the pipeline published one unpartitioned
421,412-byte Parquet file. This avoids tiny-file overhead at the measured scale.

The evidence also records pinned environment versions, all four source
checksums, quality provenance, five measurements per side, physical-plan hashes,
verified join operators, exact correctness fingerprints, the layout decision,
and explicit limitations. It uses `sha256-row-multiset-v1` and is published
atomically only after every gate passes. Failed runs retain ignored run-local
diagnostics and cannot overwrite this file.

Raw source extracts, Bronze and curated Parquet, Spark event logs, full plans,
and benchmark scratch outputs remain local and excluded from Git. The committed
checksums identify the immutable inputs without publishing Marketplace data.

Post-benchmark audit metadata is collected efficiently without changing the
measured variants: one aggregate produces both joined-row and unmatched-location
counts, and the bounded 213-row fingerprint input also supplies snapshot IDs.
The frame is intentionally not cached because warming it would contaminate the
file-layout comparison and the dedicated cache candidate was slower here.

These local-mode results demonstrate architecture and plan reasoning; they do
not show that Spark is cost-effective for this approximately 61,900-row
workload. Snowflake SQL remains the production semantic and serving path.
