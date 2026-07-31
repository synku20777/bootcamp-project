# Snowflake optimization evidence — 31 July 2026

## Decision

Keep the materialized extended serving boundary.

The controlled pre-change run showed that detailed queries were limited mainly by compilation of the layered ECDC/JHU, denominator, WDI, and window-function views. Materializing those deterministic refresh-time transformations reduced median Snowflake elapsed time by 70.7% to 90.1% across every detailed path. No detailed path crossed the 10% regression threshold.

The overview path was already served by `COUNTRY_LATEST_METRICS_EXTENDED`, so it was outside the changed boundary. Its 106 ms to 118 ms Snowflake movement is 12 ms of run-to-run variation at a sub-120 ms absolute duration; its client median still fell from 759.1 ms to 474.1 ms. It is not evidence for reverting either detailed materialization.

## Controlled method

- Environment: live Snowflake, `COVID_WH`, X-Small Gen2, `COVID_APP_ROLE`, extended dataset.
- Workload: overview, Latvia 2022 time series, Latvia country page, Latvia/Estonia/Lithuania comparison, full-range increase patterns, and Latvia 90-observation forecast history.
- Repetitions: one warmup and five measured executions per operation and phase.
- Isolation: Snowflake result-cache reuse disabled. This is a serial latency comparison, not a concurrency or throughput benchmark.
- Attribution: every statement has a run-specific tag in the form `covid-profile:<run-id>:extended:<operation>` and a Snowflake query ID.
- Timing: client duration includes connection establishment, connector work, transfer, and local row conversion. Connection establishment is saved separately in the post-change run. Snowflake elapsed, compilation, and execution time come from Information Schema query history.
- Profile evidence: each query records bytes scanned, rows returned, queue times, QAS activity, and operator-level partition pruning, local spill, and remote spill.
- Sanitization: SQL text, bind values, account identifiers, usernames, passwords, and connector URLs are not stored.

The machine-readable companion is [`performance_evidence.json`](performance_evidence.json). It contains 36 tagged query records per phase, including query IDs and operator summaries.

## Before and after medians

| Operation | Client before | Client after | Connection after | Snowflake before | Snowflake after | Compilation before | Compilation after | Execution before | Execution after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Overview | 759.1 ms | 474.1 ms | 228.6 ms | 106 ms | 118 ms | 64 ms | 89 ms | 41 ms | 27 ms |
| Time series | 1,902.9 ms | 547.5 ms | 224.8 ms | 1,471 ms | 149 ms | 1,178 ms | 117 ms | 293 ms | 33 ms |
| Country dashboard | 2,577.4 ms | 1,118.9 ms | 229.0 ms | 1,607 ms | 470 ms | 1,134 ms | 284 ms | 464 ms | 173 ms |
| Comparison | 2,228.7 ms | 1,064.1 ms | 223.7 ms | 1,582 ms | 464 ms | 1,057 ms | 310 ms | 499 ms | 153 ms |
| Patterns | 2,979.9 ms | 724.1 ms | 292.3 ms | 2,350 ms | 233 ms | 1,832 ms | 189 ms | 492 ms | 48 ms |
| Forecast history | 1,987.0 ms | 623.6 ms | 253.9 ms | 1,438 ms | 158 ms | 1,118 ms | 124 ms | 329 ms | 36 ms |

Detailed-query compilation fell by 70.7% to 90.1%. Post-change connection medians were 223.7 to 292.3 ms. Connection establishment is therefore a visible share of an uncached request after the SQL fix, but it was not the pre-change bottleneck. A pool is not added because Redis limits cache-miss frequency and the bootcamp service does not have measured sustained miss concurrency that would justify pool lifecycle and failure-state complexity.

One measured comparison request had an 8,156.9 ms client duration after a transient result-download read timeout and successful connector retry; its Snowflake statement was at most 500 ms. The median comparison client duration remained 1,064.1 ms. This outlier is retained because it demonstrates why connector/network time and Snowflake execution must not be conflated. The 30-second network bound limits this class of failure; the event does not support changing the database materialization decision.

## Scan, pruning, queue, spill, and QAS

The optimization removes repeated logical expansion; it does not claim to reduce every scan byte. Detailed query-history bytes moved from about 7.1–7.2 MB to about 8.5–9.0 MB for enriched-table consumers, while the pattern path fell to 129,536 bytes. Despite the modest enriched-table byte increase, compilation and execution both fell materially. This is direct evidence that the layered logical plan, not storage throughput, was the dominant problem.

The materialized enriched table contains 224,265 rows and 8,432,128 Snowflake-reported table bytes across eight micro-partitions. Post-change country filters scanned eight of eight partitions. Adding a cluster key, automatic clustering, or Search Optimization would therefore introduce maintenance or serverless cost to address less than 9 MB per detailed query whose Snowflake median is already 149–470 ms. The measured scale does not justify it.

Across all 30 measured post-change statements:

- maximum overload queue time: 0 ms;
- maximum provisioning queue time: 0 ms;
- maximum local spill: 0 bytes;
- maximum remote spill: 0 bytes;
- maximum QAS bytes and partitions: 0;
- all recorded table scans were served from the warehouse cache during this controlled warm comparison.

The first excluded warmup overview recorded 82 ms of provisioning queue while the warehouse resumed; the equivalent pre-change warmup recorded 94 ms. All 36 post-change statements still had zero overload queue, QAS activity, and spill. This expected resume cost is preserved in the JSON and is not hidden inside the measured medians.

QAS was enabled before publication but had zero accelerated bytes in the controlled pre-change run and zero credits in the preceding seven-day QAS history. It is now explicitly disabled. Snowflake documents QAS as shared serverless compute intended mainly for large scans and outlier queries, billed separately from warehouse use; resource monitors do not control serverless-feature spend. See [Query Acceleration Service](https://docs.snowflake.com/en/user-guide/query-acceleration-service) and [resource monitors](https://docs.snowflake.com/en/user-guide/resource-monitors).

## Result equivalence and object lifecycle

| Contract | Before | After public view | After data table | Result |
| --- | ---: | ---: | ---: | --- |
| Extended rows | 224,265 | 224,265 | 224,265 | Equal |
| Extended unordered hash | -5087670133214380742 | -5087670133214380742 | -5087670133214380742 | Equal |
| Pattern rows | 5,135 | 5,135 | 5,135 | Equal |
| Pattern unordered hash | -3299689491681660756 | -3299689491681660756 | -3299689491681660756 | Equal |
| Latest-country rows | 222 | 222 | Not applicable | Equal |
| Latest-country unordered hash | -6375024211981895237 | -6375024211981895237 | Not applicable | Equal |

The extended contract still covers 221 ISO3 countries from 31 December 2019 through 9 March 2023, has zero duplicate location/date keys, and classifies 4,194 rows with unavailable rate denominators. Pattern validation still reports zero invalid durations and zero matches below the three-increase minimum.

`COVID_ENRICHED_EXTENDED_DATA` and `CASE_INCREASE_PATTERNS_EXTENDED_DATA` are transient refresh products. The public names remain compatibility views. `sql/09_create_jhu_extension.sql` rebuilds the data tables, switches the public projections, and then rebuilds the latest-country table. Source-splice or denominator-policy changes therefore have one controlled refresh owner. Legacy ECDC-only objects remain unchanged.

## Cost controls and interpretation

The deployed warehouse is X-Small Gen2 with 60-second auto-suspend, auto-resume, QAS disabled, and `COVID_PROJECT_MONITOR` attached. The live monitor quota was temporarily raised to 25 credits for this audit; setup deliberately retains five credits as the normal bootcamp default.

The monitor changed from 4.08 used credits at the pre capture to 4.19 at the final post capture. That 0.11-credit account observation includes publication, evidence queries, operator-profile collection, and any other warehouse activity in the interval. It must not be presented as API-only cost. Query tags identify the measured statements, but warehouse metering remains time-window aggregate data.

Immediate evidence uses the Information Schema query-history function. Snowflake documents that the Account Usage `QUERY_HISTORY` view can lag by up to 45 minutes, so it is appropriate for later cost reconciliation rather than immediate pass/fail capture. See the [QUERY_HISTORY reference](https://docs.snowflake.com/en/sql-reference/account-usage/query_history).

## API controls introduced with the optimization

- Connector defaults: 10-second login timeout, 30-second network timeout, and 30-second Snowflake statement timeout.
- Query-tag format: `<configured-prefix>:<legacy-or-extended>:<repository-operation>`; application name is `COVID_ANALYTICS_API`.
- Success telemetry: operation, Snowflake query ID, connection duration, query-and-fetch duration, row count, and selected dataset. SQL, bind values, credentials, and connection URLs are excluded.
- Cache identity: every COVID-derived key includes `COVID_DATASET`; WDI-only keys remain snapshot-based; optional combined-page context uses revision `unavailable` when the manifest cannot be read.
- Stampede bounds: 60-second lock lease and 15-second waiter timeout.
- Publication sequence: publish and validate Snowflake objects, rebuild latest-country data, then clear only the Redis project prefix. The live sequence removed one `covid-api:v4` key.

A separate API-role verification returned 222 overview rows with query ID `01c6114c-0005-5a4a-0001-8afa001849c6` and the default runtime tag `covid-api:extended:dashboard_overview`. It is an observability contract check, not part of the controlled timing comparison.

Snowflake supports connector login/network timeouts and connection-time session parameters, including `QUERY_TAG`; see the [Python Connector connection guidance](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-connect).

## Reproduction

From the repository environment, run `scripts/capture_snowflake_performance.py` with `--phase pre_materialization` before publication and `--phase post_materialization` after publication. Use the same environment file and output path for both phases. Keep one warmup and five measured repetitions, do not enable Snowflake result-cache reuse, deploy with the checked-in setup/extension SQL, and clear the project Redis prefix only after equivalence checks pass.
