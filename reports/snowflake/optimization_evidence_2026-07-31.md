# Snowflake optimization evidence — 31 July 2026

## Decision

Keep the materialized extended serving boundary.

The controlled pre-change run showed that layered-view compilation limited the detailed queries. The layers include ECDC/JHU, denominator, WDI, and window-function views.

Materializing deterministic refresh-time transformations reduced median Snowflake elapsed time by 70.7% to 90.1%. No detailed path crossed the 10% regression threshold.

`COUNTRY_LATEST_METRICS_EXTENDED` already served the overview path. The overview path was outside the changed boundary.

Snowflake elapsed time changed from 106 ms to 118 ms. This 12 ms movement is normal run-to-run variation below 120 ms.

Client median time decreased from 759.1 ms to 474.1 ms. This result does not support a materialization rollback.

## Controlled method

- Environment: live Snowflake, `COVID_WH`, X-Small Gen2, `COVID_APP_ROLE`, extended dataset.
- Workload: overview, Latvia 2022 time series, Latvia country page, Latvia/Estonia/Lithuania comparison, full-range increase patterns, and Latvia 90-observation forecast history.
- Repetitions: one warmup and five measured executions per operation and phase.
- Isolation: Snowflake result-cache reuse disabled. This is a serial latency comparison, not a concurrency or throughput benchmark.
- Attribution: every statement has a run-specific tag in the form `covid-profile:<run-id>:extended:<operation>` and a Snowflake query ID.
- Timing: client duration includes connection establishment, connector work, transfer, and local row conversion. The post-change run saves connection time separately. Information Schema query history supplies Snowflake timings.
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

Detailed-query compilation decreased by 70.7% to 90.1%. Post-change connection medians were 223.7 to 292.3 ms.

Connection establishment is a visible part of an uncached request after the SQL fix. It was not the pre-change bottleneck.

The patch does not add a connection pool because Redis limits cache-miss frequency. No measurement shows sustained miss concurrency that justifies pool complexity.

One measured comparison request had an 8,156.9 ms client duration after a transient download timeout. The connector retried successfully.

Its Snowflake statement used at most 500 ms. The median comparison client duration remained 1,064.1 ms.

The evidence retains this outlier. It shows why client time and Snowflake execution time are different measures.

The 30-second network bound limits this failure type. The event does not support a change to the materialization decision.

## Scan, pruning, queue, spill, and QAS

The optimization removes repeated logical expansion. It does not claim to reduce each scanned byte.

Enriched-table query bytes changed from about 7.1–7.2 MB to about 8.5–9.0 MB. Pattern query bytes decreased to 129,536 bytes.

Compilation and execution time decreased despite the enriched-table byte increase. This result identifies the layered logical plan as the dominant problem.

The materialized enriched table contains 224,265 rows and 8,432,128 Snowflake-reported bytes across eight micro-partitions. Country filters scanned all eight partitions after the change.

A clustering key, automatic clustering, or Search Optimization would add maintenance or serverless cost. Each detailed query scans less than 9 MB.

Snowflake medians are already 149–470 ms. The measured scale does not justify these features.

Across all 30 measured post-change statements:

- Maximum overload queue time: 0 ms.
- Maximum provisioning queue time: 0 ms.
- Maximum local spill: 0 bytes.
- Maximum remote spill: 0 bytes.
- Maximum QAS bytes and partitions: 0.
- All recorded table scans used the warehouse cache during this controlled warm comparison.

The first excluded overview warmup recorded 82 ms of provisioning queue while the warehouse resumed. The equivalent pre-change warmup recorded 94 ms.

All 36 post-change statements had zero overload queue, QAS activity, and spill. The JSON retains this expected resume cost outside the measured medians.

Before publication, the warehouse configuration enabled QAS. QAS used zero accelerated bytes in the controlled run and zero credits in the preceding seven-day history.

The project now disables QAS. Snowflake bills this serverless compute separately from warehouse use.

Resource monitors do not control serverless feature costs. See [Query Acceleration Service](https://docs.snowflake.com/en/user-guide/query-acceleration-service) and [resource monitors](https://docs.snowflake.com/en/user-guide/resource-monitors).

## Result equivalence and object lifecycle

| Contract | Before | After public view | After data table | Result |
| --- | ---: | ---: | ---: | --- |
| Extended rows | 224,265 | 224,265 | 224,265 | Equal |
| Extended unordered hash | -5087670133214380742 | -5087670133214380742 | -5087670133214380742 | Equal |
| Pattern rows | 5,135 | 5,135 | 5,135 | Equal |
| Pattern unordered hash | -3299689491681660756 | -3299689491681660756 | -3299689491681660756 | Equal |
| Latest-country rows | 222 | 222 | Not applicable | Equal |
| Latest-country unordered hash | -6375024211981895237 | -6375024211981895237 | Not applicable | Equal |

The extended contract covers 221 ISO3 countries from 31 December 2019 through 9 March 2023. It has zero duplicate location and date keys.

The contract classifies 4,194 rows with unavailable rate denominators. Pattern validation reports zero invalid durations and zero matches below the three-increase minimum.

`COVID_ENRICHED_EXTENDED_DATA` and `CASE_INCREASE_PATTERNS_EXTENDED_DATA` are transient refresh products. The public names remain compatibility views. `sql/09_create_jhu_extension.sql` rebuilds the data tables, switches the public projections, and then rebuilds the latest-country table. Source-splice or denominator-policy changes therefore have one controlled refresh owner. Legacy ECDC-only objects remain unchanged.

## Cost controls and interpretation

The deployed warehouse is X-Small Gen2. It uses 60-second auto-suspend, auto-resume, disabled QAS, and `COVID_PROJECT_MONITOR`.

The live audit temporarily used a 25-credit monitor quota. Setup retains five credits as the normal bootcamp default.

The monitor changed from 4.08 used credits at the pre capture to 4.19 at the final post capture. This 0.11-credit observation includes all warehouse activity in the interval.

Do not present this value as API-only cost. Query tags identify measured statements, but warehouse metering remains time-window aggregate data.

Immediate evidence uses the Information Schema query-history function. Snowflake documents a delay of up to 45 minutes for Account Usage `QUERY_HISTORY`.

Use Account Usage for later cost reconciliation, not immediate pass or fail capture. See the [QUERY_HISTORY reference](https://docs.snowflake.com/en/sql-reference/account-usage/query_history).

## API controls introduced with the optimization

- Connector defaults: 10-second login timeout, 30-second network timeout, and 30-second Snowflake statement timeout.
- Query-tag format: `<configured-prefix>:<legacy-or-extended>:<repository-operation>`. The application name is `COVID_ANALYTICS_API`.
- Success telemetry: operation, Snowflake query ID, connection duration, query-and-fetch duration, row count, and selected dataset. Logs exclude SQL, bind values, credentials, and URLs.
- Cache identity: every COVID-derived key includes `COVID_DATASET`. WDI-only keys remain snapshot-based. Optional combined-page context uses revision `unavailable` when required.
- Stampede bounds: 60-second lock lease and 15-second waiter timeout.
- Publication sequence: publish and validate Snowflake objects, rebuild latest-country data, then clear only the Redis project prefix. The live sequence removed one `covid-api:v4` key.

A separate API-role verification returned 222 overview rows with query ID `01c6114c-0005-5a4a-0001-8afa001849c6` and the default runtime tag `covid-api:extended:dashboard_overview`. It is an observability contract check, not part of the controlled timing comparison.

Snowflake supports connector login and network timeouts. It also supports connection-time session parameters such as `QUERY_TAG`.

See the [Python Connector connection guidance](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-connect).

## Reproduction

To reproduce the measurement:

1. Run `scripts/capture_snowflake_performance.py` with `--phase pre_materialization` before publication.
2. Deploy with the checked-in setup and extension SQL.
3. Run the script with `--phase post_materialization` after publication.
4. Use the same environment file and output path for both phases.
5. Use one warmup and five measured repetitions.
6. Keep Snowflake result-cache reuse disabled.
7. Clear the Redis project prefix only after the equivalence checks pass.
