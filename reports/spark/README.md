# Spark optimization evidence

`evidence.json` is generated from the sanitized real-data benchmark run. It is
the only generated Spark artifact committed to Git.

The evidence contains environment versions, source checksums and sizes,
quality status, five warm-run measurements per variant, median/range summaries,
physical-plan hashes, verified join operators, the final file-layout decision,
exact schema/row-count/content correctness gates, and explicit limitations.
Evidence version 2 uses the versioned `sha256-row-multiset-v1` protocol and is
published atomically only when every gate passes. Failed runs retain sanitized
run-local diagnostics and cannot overwrite this authoritative file.

Raw source extracts, Bronze and curated Parquet files, Spark event logs, full
plans, and benchmark scratch outputs remain local and are excluded from Git.
The source checksum proves which immutable batch produced the summary without
publishing Marketplace data.

These measurements demonstrate architecture and execution-plan reasoning. They
must not be interpreted as evidence that Spark is cost-effective for the
approximately 61,900-row source; pandas and Snowflake SQL remain simpler for
the current scale.
