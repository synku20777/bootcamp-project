# COVID-19 Data Integration, Analysis, and Visualization Platform

Implementation report

**Student:** Nestor Kulik  
**Date:** 31 July 2026<br>
**Repository:** https://github.com/synku20777/bootcamp-project  
**Reviewed branch and commit:** `extended_0.8`, base commit `9dc71b70b0cf9e506c601804124fa81783e30139` plus this implementation working tree

## 1. Executive summary

This project integrates the free Snowflake Marketplace COVID-19 Epidemiological Data share with a versioned, checksum-verified World Development Indicators history for 2019–2021. A source-faithful active WDI snapshot supplies country context, while a separately frozen 2020 population denominator preserves existing epidemiological rates. Snowflake owns the analytical truth, PySpark demonstrates an immutable Bronze and profiling path, FastAPI exposes typed analytical and forecasting contracts, Redis protects the Snowflake trial budget, MongoDB stores user annotations, and Dash provides six interactive pages.

The implementation now covers every required in-repository functional task and the clustering bonus. Forecasting compares a 7-day mean with a recent linear trend using rolling temporal holdout. Offline Spark clustering segments ISO3 countries from five population-normalized COVID outcomes, evaluates multiple `k` values and seeds, rejects small or unstable solutions, and publishes immutable local analytical artifacts. Clustering is implemented and fixture-validated; a credentialed extended-mart run is still required before claiming authoritative real-data cluster results.

The strongest engineering qualities are reproducibility, explicit data contracts, least-privilege access, source-correction fidelity, bounded warehouse queries, fail-closed cache protection, and unusually careful Spark evidence. The project does not claim Spark is generally faster at this data volume: early projection, AQE, and caching a reused frame measured slower, while three explicit broadcasts and one appropriately sized Parquet file measured faster.

The exact Python 3.12.13, PySpark 3.5.6, and Java 17.0.19 runtime passed all 31 Spark tests, including the new feature, exclusion, stability, deterministic-labelling, local-publication, runtime-policy, broadcast-fallback, skew, metric-evidence, and complete fixture-pipeline contracts. Static checks and 14 focused export/publication tests also passed. Committed dated artifacts still record the accepted Snowflake publication, mart verification, API smoke test, migration reconciliation, and Spark evidence version 3. No credentials or extended immutable source batch were available, so fixture diagnostics did not replace that real-data evidence. A final submission should perform the credentialed five-file Spark run and a clean-VM acceptance run.

## 2. Requirement compliance

| Assignment task | Status | Implementation evidence | Qualification |
| --- | --- | --- | --- |
| 1. Marketplace data and resource monitor | Complete | Imported ECDC source contract; AWS Stockholm setup instructions; 5-credit monthly monitor; X-Small warehouse; 60-second auto-suspend | Marketplace installation remains a manual account action because it requires the student's Snowflake account acceptance |
| 2. Exploration and enhancement | Complete | Reusable SQL EDA, automated CSV exports, explicit Spark profiling, versioned WDI population, density, age, real-GDP-per-capita and health-expenditure context, normalized per-capita and mortality metrics | Pandemic-period indicator changes are descriptive and make no causal claim |
| 3. NoSQL model | Complete | MongoDB annotations with Pydantic validation, canonical analytical identity, UTC dates, and two compound indexes | No database-side JSON Schema validator; API validation is authoritative |
| 4. Python API | Complete | FastAPI queries Snowflake, reads/writes MongoDB, performs on-the-fly metrics and forecasting, and returns typed JSON | No public authentication or rate limiting |
| 5. Interactive visualization | Complete | Dash pages for status, overview, country exploration, comparison, forecasting, and annotations | Browser QA should be repeated on the final clean VM |
| 6. Analytical features | Complete plus fixture-validated bonus | Forecasting uses rolling holdout MAE/RMSE and a 1-30-day empirical interval; offline Spark clustering uses COVID-only normalized features, multi-seed silhouette/stability gates, deterministic labels, and local model artifacts | Neither model is epidemiological or causal; authoritative clustering evidence awaits a credentialed extended-mart run |
| 7. Performance optimization | Complete with evidence limitation | Snowflake monitor/auto-suspend, precomputed latest snapshot, projection, bounded date/country filters, one-statement repository budget, consolidated page payloads | No newly captured Snowflake Query Profile comparison in this review |
| 8. API caching | Complete | Redis TTLs, versioned keys, Pydantic cache revalidation, prefix-scoped invalidation, stampede lock, fail-closed behavior | Redis becomes an intentional availability dependency to protect trial credits |
| 9. Pattern identification | Complete | Snowflake `MATCH_RECOGNIZE` identifies at least three consecutive daily increases | Results are reporting patterns, not causal transmission regimes |
| 10. GitHub and configuration | Partial until final push | GitHub repository, Dockerfiles, Compose, `.env.example`, uv lockfile, CI, setup scripts, tests, and report | The final working-tree changes must be committed and pushed before submission |

## 3. Architecture

### 3.1 End-to-end data flow

1. The Snowflake Marketplace ECDC share provides country-level daily cases and deaths.
2. SQL exploration establishes the real grain, date coverage, null behavior, duplicates, and correction semantics before transformation.
3. A country mapping table resolves known source exceptions. The staging view aggregates duplicate country-date records, creates stable location keys, and derives cumulative measures while preserving negative daily corrections.
4. The World Bank path preserves immutable historical observations and a snapshot registry. A current-snapshot view selects exactly one active WDI release; identity, allowlist, year, decimal, duplicate and coverage gates run before publication.
5. `COUNTRY_COVID_DENOMINATOR` preserves the original committed 2020 population values independently. `COVID_ENRICHED` uses only that frozen policy object to derive cases and deaths per 100,000, cumulative measures, mortality percentage, join status, and data-correction flags.
6. A small transient latest-country table serves overview and identity resolution. A separate pattern view applies `MATCH_RECOGNIZE` to daily data.
7. FastAPI separates routes, services, typed models, and repositories. Every public Snowflake repository method executes one bounded statement.
8. Redis serves validated analytical responses and blocks cache stampedes. MongoDB owns user-authored annotations.
9. Dash requests combined page payloads and fans them out from browser-side stores, preventing one warehouse request per chart.
10. A separate PySpark path exports one immutable five-file source batch, publishes Bronze and curated Parquet through deterministic quality gates, records benchmark evidence, and creates offline clustering artifacts from the governed extended mart.

### 3.2 Architectural decisions and tradeoffs

#### Snowflake as analytical source of truth

**Why:** Marketplace sharing avoids copying provider-owned raw data, and SQL window, normalization, and pattern operations run close to managed compute. **Tradeoff:** the main enriched mart is a view, so general time-series requests recompute transformations. Redis and the latest-country snapshot offset that cost without introducing a second analytical truth.

#### ISO-first integration with explicit exceptions

**Why:** ISO identifiers are more stable than human country names, while a small mapping table makes known exceptions reviewable. **Tradeoff:** no reference mapping is permanently complete. The join-status column surfaces `MATCHED`, `SOURCE_UNAVAILABLE`, `NO_ISO_CODE`, and `UNMATCHED` instead of hiding uncertainty.

#### Preserve negative source corrections

**Why:** negative daily values are legitimate provider revisions. Replacing them would silently rewrite history. **Tradeoff:** cumulative curves can fall and simple pattern or forecasting logic can be sensitive to corrections. The mart exposes flags, and forecasting retains the raw history while flooring only the model's working copy.

#### Versioned WDI publication and frozen denominator

**Why:** source observations, application policy and analytical timing answer different questions. The immutable WDI history preserves source fidelity; a registry makes the selected snapshot explicit; the 2019 mart provides a pre-pandemic baseline; and the frozen denominator prevents a routine World Bank revision from silently rewriting COVID rates. **Tradeoff:** publication requires two controlled transactions, reconciliation, snapshot-aware caching and a separate denominator approval lifecycle. These controls add code but remove an otherwise hidden semantic dependency.

#### MongoDB for annotations

**Why:** comments are user-generated, semi-structured context with a lifecycle independent of the analytical mart. **Tradeoff:** Snowflake validation and MongoDB insertion cannot share a transaction; consistency is managed at the service boundary.

#### Redis as a budget gate

**Why:** on a credit-limited Snowflake trial, silently bypassing an unavailable cache can turn a dependency outage into repeated paid queries. **Tradeoff:** analytical availability depends on Redis. The decision is deliberate and visible through `503 cache_unavailable` responses.

#### Combined dashboard payloads

**Why:** one page request makes latency, caching, and warehouse cost predictable. **Tradeoff:** payloads can contain more data than a single chart needs. At the current scale, this is cheaper than network and query fan-out.

#### Spark isolated from the serving path

**Why:** Spark is mandatory in the engineering brief but unnecessary for a 61,900-row interactive workload. Isolating it demonstrates big-data engineering and supports an offline modelling bonus without adding Java startup or model latency to the API. **Tradeoff:** the ECDC benchmark transformations still require semantic-equivalence controls; clustering avoids a third splice implementation by consuming Snowflake's governed extended mart directly.

#### Transparent forecast candidates

**Why:** the source is short, historical, corrected, and non-stationary. A 7-day mean and bounded recent trend are easier to audit than a complex model and require no heavy runtime dependency. **Tradeoff:** the models cannot represent interventions, seasonality beyond the weekly mean, or structural breaks. Their output is explicitly a workflow demonstration.

## 4. Snowflake setup and data exploration

The documented account path uses AWS Europe (Stockholm). `sql/00_project_setup.sql` creates a 5-credit monthly resource monitor with notification at 50%, suspension at 80%, and immediate suspension at 100%. `COVID_WH` is X-Small, starts suspended, resumes on demand, and auto-suspends after 60 seconds. `COVID_PROJECT_ADMIN` owns deployment work; `COVID_APP_ROLE` receives only warehouse usage plus read access to the marts.

The exploration SQL inventories Marketplace objects and columns before encoding assumptions. It checks date coverage, duplicate country-date rows, null percentages, and negative daily values. The key semantic result is that `CASES` and `DEATHS` are daily measures rather than cumulative totals. Cumulative values are therefore calculated with ordered window sums, not by subtracting adjacent source rows.

The live snapshot verified on 26 July 2026 contained 61,900 daily rows across 214 locations from 31 December 2019 through 14 December 2020. Population matched 203 locations and 59,336 rows; 11 source-unavailable locations accounted for 2,564 rows. These values are evidence from the deployed snapshot, not universal properties of every future Marketplace refresh.

Automated EDA has two levels. `scripts/run_eda.py` exports coverage, missing population, correction, and latest-country CSVs from the Snowflake mart. The PySpark entry point adds explicit-schema profiling, null and distinct counts, numeric summaries, bounds, duplicate checks, schema-drift detection, and machine-readable quality publication.

## 5. Data enhancement and analytical model

The committed WDI snapshot contains 3,255 observations: 217 non-aggregate economies, five indicators and three years. The 29 July 2026 manifest records 102 null indicator values, no duplicate candidate keys, no identity conflicts, and source last-update date 13 July 2026. Observed minimum coverage was 100% for population and population aged 65+, 99.54% for density, 95.85% for real GDP per capita and 88.94% for PPP health expenditure. Each indicator exceeded its configured publication threshold.

The selected indicators are deliberately non-duplicate analytical variables: `SP.POP.TOTL`, `EN.POP.DNST`, `SP.POP.65UP.TO.ZS`, `NY.GDP.PCAP.KD`, and `SH.XPD.CHEX.PP.CD`. Real GDP per capita is labelled in constant 2015 US dollars and never called total GDP or pandemic impact. Health expenditure is labelled “Current health expenditure per capita, PPP — current international $, 2019”; no health-spending change is calculated because this current-price PPP series is not a real inflation-adjusted time series.

The 2019 values serve as explanatory country context measured before the pandemic. The 2020 and 2021 real-GDP-per-capita values are retained to describe change during the pandemic period. The names `REAL_GDP_PER_CAPITA_CHANGE_*` intentionally avoid causal “impact” language. Population context from the active 2020 WDI observation and the frozen 2020 COVID rate denominator are exposed as separate fields.

The analytical model deliberately favors a narrow serving mart over a full star schema because the source already has a simple country-date grain:

| Object | Type | Purpose |
| --- | --- | --- |
| `RAW.WORLD_BANK_COUNTRY_INDICATORS` | Permanent table | Immutable WDI observations across historical snapshots |
| `RAW.WORLD_BANK_INDICATOR_SNAPSHOTS` | Permanent table | Publication state, provenance, active snapshot and rollback predecessor |
| `STAGING.WORLD_BANK_COUNTRY_INDICATORS_CURRENT` | View | Exactly the one active source snapshot |
| `STAGING.WORLD_BANK_COUNTRY_INDICATORS_CLEAN` | View | Accepted identities, allowlist, years, names and units |
| `MARTS.DIM_COUNTRY` | Transient table | Canonical COVID country universe and context eligibility |
| `MARTS.COUNTRY_COVID_DENOMINATOR` | Permanent table | Frozen population policy for per-capita COVID rates |
| `MARTS.COUNTRY_COVID_DENOMINATOR_HISTORY` | Permanent table | Approved denominator versions retained for rollback |
| `MARTS.COUNTRY_BASELINE_2019` | Transient table | One context-eligible country row with values and missing statuses |
| `MARTS.COUNTRY_INDICATOR_ANNUAL` | Transient table | One country/year row retaining all five indicators |
| `STAGING.COVID_COUNTRY_DAILY` | View | Clean daily grain, stable key, cumulative metrics, correction flags |
| `MARTS.COVID_ENRICHED` | View | Frozen-denominator join, per-capita metrics, mortality, join status |
| `MARTS.COUNTRY_LATEST_METRICS` | Transient table | Small precomputed snapshot for overview and identity lookups |
| `MARTS.COUNTRY_CONTEXT_ANALYSIS` | View | Baseline, latest COVID metrics and descriptive real-GDP-per-capita changes |
| `MARTS.CASE_INCREASE_PATTERNS` | View | Sustained daily-increase pattern results |

`NULLIF` prevents division by zero. Missing observations and zero are never conflated. Per-capita metrics use the frozen denominator and mortality uses cumulative deaths divided by cumulative confirmed cases. These are reported-data indicators, not estimates of infections or infection fatality.

Live Snowflake verification accepted active snapshot `wdi2-2019-2021-372906f371e0391f` with 3,255 observations, 203 frozen-denominator countries, 213 context-eligible baseline rows, 639 annual rows, 213 context rows, and zero duplicate observation keys. Migration reconciliation compared 61,836 canonical ISO3/date rows: no keys were missing, all case, death and denominator values matched exactly, and every per-capita and mortality difference was zero. The 64 old and 64 new rows without canonical ISO3 were reported separately rather than being forced into an unsafe name join.

The explicit denominator planner compared all 203 frozen countries and 59,336 covered COVID rows to the active WDI population. It found zero population or rate differences, so the proposed `v2` plan was correctly marked unapproved: a source refresh cannot manufacture a new denominator version when no policy value changed.

## 6. NoSQL design

MongoDB collection: `covid_app.annotations`.

```json
{
  "_id": "ObjectId",
  "country": "Latvia",
  "iso2": "LV",
  "iso3": "LVA",
  "location_key": "ISO2:LV",
  "report_date": "2020-03-15T00:00:00Z",
  "metric": "new_cases",
  "comment": "Large spike may reflect a reporting delay.",
  "created_by": "student",
  "created_at": "2026-07-29T12:00:00Z"
}
```

The API resolves the submitted country against Snowflake and verifies that the date exists before insertion. Canonical identity fields avoid separate comments for `LV`, `LVA`, and `Latvia`. Dates are normalized to UTC midnight. Comments are trimmed and limited to 1,000 characters; creator names are limited to 80.

Two non-unique compound indexes support chronological country/date reads and metric-filtered date ranges. They are intentionally non-unique so multiple users can discuss the same point. In a public product, authentication, moderation, pagination, edit/delete operations, and a MongoDB JSON Schema validator would be required.

## 7. API implementation and caching

FastAPI exposes liveness, readiness, an explicit Snowflake health check, overview, country summary, country context, time series, comparison, combined dashboard payloads, forecasts, and annotation create/list operations. Swagger documentation is available at `/docs`.

`GET /countries/{identifier}/context` returns metadata-rich baseline, annual and change fields. Each indicator carries value, missing status, year, unit, code and snapshot ID. The combined Country Explorer response carries the same object without another browser request. The methodology is explicitly `descriptive` and warns that pandemic-period changes do not establish causality.

Security and correctness controls include Pydantic request/response contracts, enum-based metric selection, bind parameters for user values, a least-privilege Snowflake role, sanitized dependency errors, structured request IDs, and no credential fields in dashboard settings. Metric names are interpolated only after an enum-to-column allowlist lookup.

The forecast endpoint is:

```text
GET /forecast?country=LV&metric=new_cases&days=30&lookback_days=90
```

It accepts daily cases or deaths, a 1-30-day horizon, and a 42-180-observation window. Snowflake applies the history limit before data crosses the network. The response includes reported history, predictions, lower and upper bounds, both candidates' MAE/RMSE, the selected model, and caveats.

Redis uses versioned canonical keys and validates cached JSON back into the declared Pydantic model. Context keys have the visible namespace `covid-api:v3:<snapshot-id>:country-context:<iso3>`, so a newly deployed snapshot cannot reuse stale country context. Stable analytical payloads default to 24 hours; forecasts default to 6 hours. A per-key lease prevents concurrent misses from duplicating a Snowflake query. Prefix-scoped invalidation uses incremental `SCAN`, never database-wide `FLUSHDB`.

The committed manifest is loaded at application startup. If it differs from Snowflake's active snapshot, the context endpoint alone returns `503 context_data_unavailable`. COVID summary, time-series and forecast routes remain available, and the Country Explorer renders epidemiological content with a context warning. This failure boundary prevents optional context rollout from becoming a platform-wide outage.

## 8. Dashboard implementation

The Dash application provides:

1. **Status:** cheap API, MongoDB, and Redis checks; Snowflake is queried only by an explicit button.
2. **Overview:** global KPIs, top-ten case/death bars, and cases-per-100,000 choropleth.
3. **Country Explorer:** country, metric, and date controls; selected metric, daily case/death and mortality charts; explicit COVID denominator; 2019 baseline cards; 2020 population context; and descriptive real-GDP-per-capita changes.
4. **Comparison:** two to ten countries across cases per 100,000, deaths per 100,000, and mortality, followed by a five-metric WDI baseline comparison.
5. **Forecast:** model choice, holdout MAE/RMSE, history, 1-30-day forecast, and empirical interval.
6. **Annotations:** create and filter comments stored in MongoDB.

Country Explorer keeps the COVID outcome hierarchy first: COVID KPIs precede a compact five-card WDI baseline row, and the four primary COVID charts precede the larger GDP panel. The GDP chart includes zero in its value scale and delegates change interpretation to signed, two-decimal badges so three annual observations do not exaggerate small movements. Cards use fixed, unit-specific precision and show missing source observations as unavailable rather than zero.

The WDI section uses the context already present in the combined Country Explorer payload, so it adds no browser request or Snowflake statement. Population, density, age and health expenditure remain baseline cards; only real GDP per capita has public annual history for 2019-2021. One section footer identifies the active WDI snapshot without repeating operational metadata on every card.

Comparison also keeps the COVID analysis first. Its three epidemiological charts precede a WDI selector, a horizontal bar chart, and an exact-value matrix. The chart includes zero in the value scale to prevent small cross-country differences from appearing larger than they are. The matrix shows all five baseline values with fixed precision: whole population, one-decimal density, one-decimal age share, and whole currency units. Missing source observations remain `Not available`; they are never converted to zero. WDI population is labelled as context and is not presented as the frozen COVID rate denominator. The section uses descriptive language, omits GDP changes and annual histories, and shows the WDI snapshot footer once.

The Comparison API contract adds optional baseline context to each existing country series. Snowflake supplies COVID observations and the one-row-per-ISO3 baseline through one parameterized statement. This join transfers repeated baseline columns across the daily result, but it avoids up to ten extra warehouse queries and preserves one browser request for the page. The service validates every joined row against the committed WDI snapshot. A missing or mismatched context is isolated to that country, while COVID results remain available. `countries_without_data` still refers only to missing COVID observations. Comparison cache contract version 2 includes the committed snapshot ID, so responses cannot cross WDI publications. Selector changes rerender from the page store and do not call the API or Snowflake again.

Responsive Mantine components, loading overlays, empty states, error alerts, accessible status roles, and a compact/mobile sidebar support usability. Page-level `dcc.Store` objects ensure render callbacks do not call the API again. A remaining analytical-UX improvement is explicit source/methodology text on every chart and download controls for reproducible offline analysis.

## 9. Forecasting methodology and result

The modelling copy uses non-negative incident counts; negative source corrections remain in returned history. The 7-day candidate uses the mean of the most recent week. The trend candidate uses ordinary least squares over at most the most recent 42 observations and actual date offsets, which prevents an early-pandemic regime from dominating a recent forecast and avoids inventing zeros for missing dates.

The final 14 observations form a rolling-origin, one-step-ahead holdout. At each validation date, both candidates see only earlier data. Random train/test splitting was rejected because it leaks future regimes into past predictions. The candidate with lower MAE wins; an exact tie selects the simpler weekly mean. RMSE is also reported so large misses remain visible.

For Latvia's latest 90 daily case observations, verified live on 29 July 2026, the history ran from 16 September through 14 December 2020. The 7-day mean won with holdout MAE 160.827 and RMSE 214.183; the trend scored MAE 173.407 and RMSE 225.542. The first forecast was 623.143 cases for 15 December 2020 with an empirical band of 210.429 to 1,035.857. The 30th point remained 623.143 with a widened band of 0 to 1,559.091. The widening is intentionally conservative and demonstrates why long-horizon projections from this baseline should not be over-interpreted.

The interval uses the larger of the selected model's holdout RMSE and nearest-rank 90th-percentile absolute error, widened by the square root of forecast horizon. It is descriptive, not a calibrated probabilistic confidence interval. The data ends in 2020, so no forecast in this project is current public-health guidance.

### 9.1 Offline clustering bonus

Snowflake remains responsible for the governed ECDC-to-JHU cutover. Spark consumes `COVID_ANALYTICS.MARTS.COVID_ENRICHED_EXTENDED` as a fifth immutable input only for clustering; the original ECDC input remains the transformation and benchmark fact. This prevents the same splice boundary from being reimplemented in a second engine.

ISO3 is the analytical unit. A country is eligible only with a positive governed population denominator, complete normalized measures, and at least 180 distinct daily observations. Exclusions are retained by reason. Negative corrections are preserved in source and Bronze data, while incident rates are floored at zero only in the modelling copy. Complete calendar-based 14-day windows then produce five COVID-only features: latest cumulative cases and deaths per 100,000, peak 14-day mean cases and deaths per 100,000, and volatility of the 14-day mean case rate.

The five non-negative features receive `log1p` and standardization before Spark ML KMeans. WDI variables do not enter the feature vector; they are joined after fitting only to create descriptive local profiles. This supports interpretation without implying that density, age, GDP, or health expenditure caused cluster membership.

Model selection evaluates `k=2..6` across seeds 13, 29, 47, 71, and 97. A seed run is rejected when any cluster is smaller than `max(3, 2% of eligible countries)`. Publication requires at least four valid seeds, positive median silhouette, and median pairwise Adjusted Rand Index of at least 0.75. Highest median silhouette wins; candidates within 0.01 use the smaller `k`. The authoritative seed is closest to the candidate median, lower seed breaks ties, and arbitrary predictions are relabelled by ascending standardized centroid burden.

Immutable ignored output contains assignments, raw/log/standardized features, centroid distance, exclusions, WDI profiles, fitted scaler, and selected KMeans model. Publishable diagnostics contain aggregate eligibility, candidate, stability, cluster-size, assignment-checksum, runtime, skew, spill, and lineage evidence only. Country assignments, daily rows, and WDI cluster profiles are not committed.

The implementation passed fixture validation in the pinned runtime. No credentials or checksum-verified extended source batch were available in this worktree, so no country-level result is interpreted here and no version 4 evidence is claimed. The model segments historical reporting outcomes; it does not discover causal, policy, or epidemiological regimes.

## 10. Pattern recognition

`CASE_INCREASE_PATTERNS` uses Snowflake `MATCH_RECOGNIZE` to find a start day followed by at least three consecutive calendar days where daily cases exceed the previous day. Requiring a one-day date difference prevents a gap from masquerading as an uninterrupted run.

The verified snapshot produced 1,769 matches across 170 locations. Examples include Russian Federation with 23 consecutive increases from 14 September to 7 October 2020, Spain with 20 from 24 February to 15 March 2020, and the United States with 16 from 8 to 24 March 2020. These are reproducible reporting patterns. They do not prove transmission mechanisms, policy effects, or clinical severity.

## 11. Performance optimization

### 11.1 Snowflake and API path

The warehouse is intentionally X-Small with 60-second auto-suspend because the project is interactive and low volume. A monthly resource monitor bounds total cost. The latest-country transient table reduces overview and identity work to roughly one row per location. Time-series queries project named columns, filter by resolved location and date, and never use `SELECT *`. Forecast history is capped in Snowflake. Combined page endpoints and 24-hour/6-hour cache policies reduce repeated scans. The Comparison endpoint joins the narrow, one-row-per-country WDI baseline into its existing parameterized COVID statement. This repeats five baseline values across daily rows, but it is cheaper and more predictable for an interactive page than a browser fan-out or up to ten additional Snowflake statements. Contract version 2 and the committed WDI snapshot ID form part of the Comparison cache identity.

No clustering key, materialized view, or Search Optimization Service is configured. At 61,900 mart rows, their maintenance and credit cost would likely exceed pruning benefits. This is an optimization decision, not an omission. A future scale trigger should use Query Profile evidence: bytes scanned, partitions pruned, latency percentiles, and credits per representative endpoint.

`COUNTRY_LATEST_METRICS` is atomically replaced from the verified enriched view, so readers never observe an intentionally empty snapshot. WDI publication first commits immutable candidate observations and an inactive registry row, then performs a separate controlled activation transaction. If downstream mart verification fails and a predecessor exists, bootstrap reactivates it and rebuilds the previous marts. This favors last-known-good availability over minimal orchestration.

### 11.2 Spark optimization evidence

Evidence version 3 was generated on 30 July 2026 in the rebuilt pinned image with Python 3.12.13, PySpark 3.5.6, Java 17.0.19, `local[2]`, adaptive execution enabled, and 32 shuffle partitions. It uses immutable source batch `wdi-context-qa-v1`: 61,900 ECDC rows, 217 frozen population rows, 14 explicit mapping rows, and 3,255 WDI observations from snapshot `wdi2-2019-2021-372906f371e0391f`. All four file checksums and the combined batch checksum passed before Spark initialization.

That committed evidence predates the extended fifth input, runtime hardening, and clustering stage. It remains the accepted real-data record and was deliberately not rewritten by synthetic fixtures. Version 4 requires a new credentialed export whose extended entry records selected Snowflake object, row count, date range, country count, byte count, and checksum.

Spark derives the same context-eligible country universe as Snowflake before left joining the WDI baseline. This distinction retains countries that have a valid ISO3 identity but no WDI observation instead of silently shrinking the comparison to matched rows. The resulting projection matched the accepted Snowflake artifact exactly: 213 rows, 20,199 canonical bytes, and SHA-256 `6fa8fc8208d748a8dfbd2b4d606eb09cf2faae59869dfa52c62f3b3913872d83`. The optimized physical plan recorded three build-right broadcast hash joins for country mapping, frozen population, and the narrow one-row-per-country WDI baseline.

| Comparison | Baseline median | Candidate median | Result | Engineering conclusion |
| --- | ---: | ---: | --- | --- |
| Early projection/filter | 183.939 ms | 216.075 ms | Candidate was not faster | Keep projection for contract discipline and reduced downstream width, not as a local-speed claim |
| Three broadcast joins | 762.517 ms | 561.944 ms | Candidate was faster | The historical run supports the three broadcasts at captured sizes; the new runtime gates each hint from manifest bytes and validates the resulting plan |
| Adaptive duplicate aggregation | 347.056 ms | 426.586 ms | Candidate was not faster | AQE remains a scale-safety setting; do not claim a speedup for this fixture |
| Reused-frame cache | 323.322 ms | 372.224 ms | Candidate was not faster | Cache only when reuse, recomputation cost, and memory pressure justify materialization; this run does not justify it |
| File layout | 2,787.065 ms | 1,645.820 ms | Candidate was faster | One output file avoids small-file overhead at this measured size |

Every comparison passed schema, row-count, and row-multiset checksum gates before timing and used one warm-up plus five measured repetitions per variant. Broadcast joins and file layout were the only faster candidates in this run. Early projection, AQE, and caching were slower and are not presented as elapsed-time optimizations.

The version 3 calibration measured 1,891,286 bytes. Thirteen proportionally estimated monthly partitions had a median of only 191,542 bytes, far below the 128 MiB target, so the pipeline published one unpartitioned 421,412-byte Parquet file. The hardened implementation now writes temporary month-partitioned Parquet and measures each actual compressed directory before deciding. No real-data result is claimed for that replacement until version 4 is published.

Runtime policy is now isolated from the large orchestration module. It derives bounded shuffle partitions from source bytes and advisory partition size while recording any override. Mapping, population, and WDI broadcasts are enabled only below a configurable manifest-size limit; automatic benchmark broadcast remains disabled so physical-plan assertions are deterministic. Bronze and curated target file counts use byte thresholds, coalescing only when reducing partitions and repartitioning only when increasing them.

Country-key skew evidence records median, p95, maximum rows per key, maximum share, and maximum-to-median ratio. A ratio above 5 or one key above 10% is a warning; the current design does not introduce salting without measured task imbalance. Event-log summaries now add memory spill, disk spill, peak execution memory, executor runtime, and JVM garbage-collection time to the existing input and shuffle measures.

The evidence path now consolidates joined-row and unmatched-location metrics into one post-benchmark aggregate and reuses the bounded 213-row fingerprint input to derive snapshot IDs. This removes redundant Spark actions without changing timed variants. It deliberately does not cache the enriched frame: pre-materialization would warm the file-layout input, undermine comparison isolation, and contradict the measured cache tradeoff for this workload.

Explicit schemas and header inspection fail before Bronze publication on drift. The WDI schema uses fixed decimal precision and the canonical `\N` null token. The extended contract additionally gates duplicate country-dates, missing canonical identity, invalid denominator, null modelling measures, insufficient history, and inconsistent source/segment pairs. Corrupt rows and quality failures remain run-local evidence. Immutable source, snapshot, ingestion, benchmark, and model IDs prevent silent overwrite.

Fixture runs publish ignored preview evidence only. A real `snowflake_export` run may atomically publish version 4 evidence and clustering diagnostics after quality, correctness, physical-plan, eligibility, minimum-cluster-size, silhouette, and stability gates all pass. Publishable clustering diagnostics contain no country assignments or WDI profiles. A failed model-selection or publication gate preserves the accepted version 3 evidence.

The quality status was `WARN`, not `FAIL`: 339 ECDC rows lacked a source ISO value, and 18 negative case corrections plus 8 negative death corrections were retained. These are expected policy classifications; every fail-severity gate passed and curated publication succeeded. Atomic publication replaced the authoritative evidence only after these checks, the cross-engine fingerprint, all five correctness gates, and physical-plan validation passed.

The key lesson is that Spark optimization is workload-specific. Version 3 supports broadcast joins and consolidated file layout at the captured scale, but not projection, AQE, or caching as speed claims. The new controls and offline model are fixture-validated implementation evidence, not measured real-data or distributed-scale claims. Snowflake SQL remains the production semantic and serving path.

## 12. COVID-19 insights

All findings describe the historical snapshot ending 14 December 2020.

1. Latest-location totals were 71,503,614 confirmed cases and 1,612,833 deaths, a reported cumulative case-fatality ratio of 2.2556%. Because each location contributes its own latest available row, the snapshot is not perfectly synchronized.
2. Cases per 100,000 were highest for Andorra (9,483.1), Montenegro (6,611.3), and Luxembourg (6,546.8). Small denominators make normalized burden very different from absolute burden.
3. Deaths per 100,000 were highest for Belgium (155.6), San Marino (146.7), and North Macedonia (114.3), showing how per-capita severity can be hidden by raw totals.
4. Among locations with at least 10,000 cases, reported mortality was highest for Mexico (9.1159%), Ecuador (6.8651%), and Sudan (6.2985%). Testing access, outcome lag, and attribution differences prevent a causal ranking of healthcare quality.
5. Lithuania recorded 3,381.1 cases per 100,000 versus Estonia at 1,358.0 and Latvia at 1,351.0. Lithuania's recorded incidence was about 2.5 times the other Baltic states, while reported mortality ranked differently: Latvia 1.3593%, Lithuania 0.8682%, Estonia 0.8253%.
6. Eighteen negative-case rows and eight negative-death rows occurred across 17 locations. Correction handling is therefore materially important, not a theoretical edge case.
7. The 1,769 sustained-increase patterns across 170 locations show repeated bursts in reported daily cases, but the algorithm detects monotonic reporting sequences rather than epidemiological regimes.

Confirmed cases are not infections. Countries differed in testing availability, reporting definitions, weekend effects, backfills, and death attribution. These caveats apply to charts, rankings, patterns, and forecasts.

## 13. Code quality, testing, and deployment

The codebase separates API routes, Pydantic contracts, services, repositories, dashboard components, Spark transformations, SQL, setup scripts, and tests. Non-obvious comments explain why decisions exist: fail-closed cache behavior, immutable publication, source corrections, atomic swaps, temporal validation, empirical intervals, bounded history, broadcast choice, persistence, and file layout. Comments avoid narrating readable syntax.

Dependencies are declared in `pyproject.toml` and resolved in `uv.lock`. Python 3.12 patch compatibility is declared in package metadata, while `.python-version` and Docker pin 3.12.13. Java and PySpark live only in the optional Spark image/group. GitHub Actions runs lock verification, isort, Black, Ruff, the application suite, and the Spark fixture suite.

Verification through 31 July 2026:

| Check | Result |
| --- | --- |
| Previously accepted application/unit/contract suite | 101 passed; unchanged historical review evidence |
| Changed-path export, event-metric, schema, and publication tests | 14 passed in this worktree |
| Ruff | Passed |
| isort and Black | Passed |
| Compose configuration | Parsed successfully |
| Dated Snowflake WDI publication and mart verification | Passed; committed evidence in `reports/world_bank/snowflake_verification.json` |
| Dated legacy/new COVID reconciliation | Passed; 61,836 exact canonical rows and zero tolerance failures |
| Dated context/API smoke | Passed with `COVID_APP_ROLE`; combined page context available |
| Snowflake context export fingerprint | Passed; immutable batch records 213 baseline rows |
| Pinned Docker Spark image | Previously built successfully; current dependencies unchanged |
| Exact pinned Spark runtime suite | 31 passed with Python 3.12.13, PySpark 3.5.6, and Java 17.0.19 |
| Committed Spark evidence | Version 3; four source checksums, five correctness gates, three broadcasts, 213-row cross-engine match, and curated publication passed |
| Extended clustering evidence | Implementation and fixtures passed; credentialed five-file version 4 run pending |

The normal supervisor path requires only Docker plus a Snowflake account. `setup.ps1` or `setup.sh` invokes the containerized bootstrap, validates prerequisites and postconditions, publishes the committed WDI snapshot without a network request, preserves or seeds the frozen denominator, deploys marts, starts dependencies, creates MongoDB indexes, and performs smoke checks. Manual recovery instructions are also documented.

## 14. Limitations and prioritized next work

Before submission:

1. Confirm the spelling of the student name, commit all working-tree changes, push `extended_0.8`, and replace the base commit reference with the final hash.
2. Run the documented setup on a clean virtual machine with Docker Desktop/Engine and capture healthy API, annotation round-trip, forecast, and dashboard evidence.
3. Run the credentialed five-file Spark export and pipeline; publish version 4 only if all extended quality, benchmark, silhouette, stability, and publication gates pass.
4. Retain the final CI link and Spark evidence artifacts with the submitted commit.

Engineering follow-ups:

1. Add a production Compose override without bind mounts or reload, then add TLS, secret injection, authentication, and rate limiting.
2. Add a MongoDB JSON Schema validator and integration tests with disposable Redis/MongoDB containers.
3. Add Snowflake query tags, statement timeouts, Query Profile captures, and refresh orchestration with cache invalidation.
4. Add chart-level source notes, correction markers, downloads, and synchronized-date options.
5. Reassess clustering features and thresholds only from real extended-data diagnostics; do not interpret fixture segments or treat descriptive WDI profiles causally.

## 15. Conclusion

The project is a defensible end-to-end data engineering submission. It combines a managed warehouse, external data integration, schema and quality controls, a cost-aware API, operational NoSQL data, interactive visualization, required forecasting, pattern recognition, and measured Spark engineering. Its most senior characteristic is not the number of technologies; it is that tradeoffs and negative benchmark results are documented instead of being hidden.

The remaining submission risk is operational evidence, not missing core functionality: the final working tree must be pushed, a clean-VM run should prove the complete stack, and a credentialed immutable extended batch must produce version 4 before the clustering bonus is presented as demonstrated on real data.

## Appendix A. Key commands

```bash
# Guided first-time setup
./setup.sh

# Application quality checks
uv run isort --check-only --diff .
uv run black --check --diff .
uv run ruff check .
uv run python -m unittest discover -s tests -v

# Intentional WDI refresh and offline publication are separate workflows
uv run python -m scripts.world_bank_indicators refresh
uv run python -m scripts.world_bank_indicators publish

# Spark quality and benchmark path
docker compose --profile spark run --rm spark ingest-profile \
  --source-batch-id wdi-context-v1 \
  --ingestion-id bronze-v1 \
  --benchmark-run-id benchmark-v1

docker compose --profile spark run --rm spark benchmark \
  --ingestion-id bronze-v1 \
  --benchmark-run-id benchmark-v2

# Runtime acceptance
curl -i http://localhost:8000/health/live
curl -i http://localhost:8000/health/ready
curl -i http://localhost:8000/health/snowflake
curl -i "http://localhost:8000/forecast?country=LV&metric=new_cases&days=30&lookback_days=90"
```

## Appendix B. Evidence map

| Claim | Primary repository evidence |
| --- | --- |
| Snowflake account objects and least privilege | `sql/00_project_setup.sql`, `sql/00_project_objects.sql` |
| Exploration and source semantics | `sql/01_data_exploration.sql`, `scripts/run_eda.py` |
| Country normalization and mart | `sql/02_create_country_mapping.sql` through `sql/05_create_enriched_view.sql` |
| Snapshot and pattern recognition | `sql/06_create_reporting_objects.sql`, `sql/07_analysis_queries.sql` |
| Versioned WDI publication and checksums | `app/world_bank.py`, `scripts/world_bank_indicators.py`, `tests/test_world_bank_indicators.py` |
| Frozen COVID denominator and migration | `sql/04_create_world_bank_context.sql`, `scripts/reconcile_world_bank_migration.py`, `reports/world_bank` |
| API, caching, and errors | `app/api`, `app/services`, `app/repositories`, `tests/test_api.py`, `tests/test_cache.py` |
| Forecasting | `app/services/forecasting.py`, `app/services/covid_service.py`, `tests/test_forecasting.py` |
| MongoDB annotations | `app/repositories/annotation_repository.py`, `app/services/annotation_service.py`, `tests/test_annotations.py` |
| Dashboard | `app/dashboard`, `tests/test_dashboard.py` |
| Spark Bronze, profiling, cross-engine fingerprint, optimization, and offline clustering | `app/spark_pipeline`, `scripts/export_spark_sources.py`, `spark_tests`, `reports/spark/evidence.json`, `reports/spark/README.md` |
| Reproducible deployment | `README.md`, `.env.example`, `compose.yaml`, Dockerfiles, `setup.ps1`, `setup.sh` |
