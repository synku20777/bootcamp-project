# COVID-19 Data Integration, Analysis, and Visualization Platform

Implementation report

**Student:** Nestor Kulik  
**Date:** 31 July 2026<br>
**Repository:** https://github.com/synku20777/bootcamp-project  
**Reviewed merge:** `extended_0.8` at `b634bf5`. This report includes the documentation reconciliation in the current working tree.

## 1. Executive summary

This project integrates the free Snowflake Marketplace COVID-19 data with a normalized ECDC/JHU country series through March 2023. It also integrates a checksum-verified WDI history for 2019–2021.

An active WDI snapshot supplies country context. A separate frozen 2020 population denominator preserves published epidemiological rates.

Snowflake owns the analytical truth. PySpark provides an immutable Bronze and profiling path. FastAPI exposes typed analytical and forecasting contracts.

Redis protects the Snowflake trial budget. MongoDB stores user annotations. Dash provides seven interactive pages.

The implementation covers each required repository task and the clustering bonus. Forecasting compares a 7-day mean with a recent linear trend through rolling temporal holdout.

Offline Spark clustering uses five population-normalized COVID outcomes. It evaluates multiple `k` values and seeds and rejects small or unstable solutions.

The pipeline publishes immutable local analytical artifacts. Fixture data validates the clustering implementation.

A credentialed extended-mart run is still required before the project can claim authoritative real-data cluster results.

The main engineering strengths are reproducibility, explicit data contracts, least-privilege access, source-correction fidelity, and bounded warehouse queries. They also include fail-closed cache protection and measured Snowflake and Spark decisions.

The project does not claim that Spark is generally faster at this volume. Early projection, AQE, and a reused-frame cache measured slower.

Three explicit broadcasts and one correctly sized Parquet file measured faster. The Snowflake review uses the same evidence rule.

It materializes the measured compilation bottleneck. It rejects Snowflake clustering keys, Search Optimization, QAS, and connection pooling at the measured scale.

At review time, 101 application regression checks passed. The pinned Spark image also passed its recorded regression suite.

Ruff, isort, Black, and Docker Compose validation passed. These checks are regression controls, not the basis for the architectural conclusions.

The Task 7 assessment uses tagged live Snowflake history, operator profiles, object hashes, and warehouse state. Dated artifacts record WDI publication, API smoke, migration reconciliation, and cross-engine gates.

A final submission should include one clean-VM acceptance run of the complete platform.

## 2. Requirement compliance

| Assignment task | Status | Implementation evidence | Qualification |
| --- | --- | --- | --- |
| 1. Marketplace data and resource monitor | Complete | Imported ECDC/JHU source contract. AWS Stockholm setup. Five-credit normal monitor. X-Small Gen2 warehouse. 60-second auto-suspend. QAS disabled. | The live audit temporarily used 25 credits. Marketplace installation remains a manual account action. |
| 2. Exploration and enhancement | Complete | Reusable SQL EDA, automated CSV exports, explicit Spark profiling, versioned WDI population, density, age, real-GDP-per-capita and health-expenditure context, normalized per-capita and mortality metrics | Pandemic-period indicator changes are descriptive and make no causal claim |
| 3. NoSQL model | Complete | MongoDB annotations with Pydantic validation, canonical analytical identity, UTC dates, and two compound indexes | No database-side JSON Schema validator. API validation is authoritative. |
| 4. Python API | Complete | FastAPI queries Snowflake, reads/writes MongoDB, performs on-the-fly metrics and forecasting, and returns typed JSON | No public authentication or rate limiting |
| 5. Interactive visualization | Complete | Dash pages for status, overview, country exploration, comparison, forecasting, increase patterns, and annotations | Repeat browser QA on the final clean VM. |
| 6. Time-series forecasting | Complete | 7-day mean versus recent linear trend, rolling holdout, MAE/RMSE, 1-30-day horizon, empirical interval, API and dashboard | This is an interpretable baseline, not an epidemiological model. Fixture data validates offline clustering. The credentialed extended-data run is pending. |
| 7. Performance optimization | Complete with live evidence | Materialized extended serving boundary, stable public views, precomputed latest snapshot, bounded queries, tagged Query Profile evidence, deterministic X-Small Gen2 policy | Detailed-query Snowflake medians decreased by 70.7% to 90.1%. The measured scale does not justify a Snowflake clustering key or Search Optimization. |
| 8. API caching | Complete | Redis TTLs, dataset- and snapshot-aware keys, Pydantic cache revalidation, prefix-scoped invalidation, 60-second lease, 15-second waiter, fail-closed behavior | Redis becomes an intentional availability dependency to protect trial credits |
| 9. Pattern identification | Complete | Snowflake `MATCH_RECOGNIZE` identifies at least three consecutive daily increases | Results are reporting patterns, not causal transmission regimes |
| 10. GitHub and configuration | Partial until final push | GitHub repository, Dockerfiles, Compose, `.env.example`, uv lockfile, CI, setup scripts, tests, and report | Commit and push the final working-tree changes before submission. |

## 3. Architecture

### 3.1 End-to-end data flow

1. The Snowflake Marketplace ECDC share provides country-level daily cases and deaths.
2. SQL exploration establishes the real grain, date coverage, null behavior, duplicates, and correction semantics before transformation.
3. A country mapping table resolves known source exceptions. The staging view aggregates duplicate country-date records, creates stable location keys, and derives cumulative measures while preserving negative daily corrections.
4. The World Bank path preserves immutable historical observations and a snapshot registry. A current-snapshot view selects exactly one active WDI release. Identity, allowlist, year, decimal, duplicate, and coverage gates run before publication.
5. `COUNTRY_COVID_DENOMINATOR` preserves the original committed 2020 population values independently. `COVID_ENRICHED` uses only that frozen policy object to derive cases and deaths per 100,000, cumulative measures, mortality percentage, join status, and data-correction flags.
6. A small transient latest-country table serves overview and identity resolution. Controlled publication materializes source splice, enrichment, window, and `MATCH_RECOGNIZE` work. Stable public views preserve the consumer contract.
7. FastAPI separates routes, services, typed models, and repositories. Every public Snowflake repository method executes one bounded statement.
8. Redis serves validated analytical responses and blocks cache stampedes. MongoDB owns user-authored annotations.
9. Dash requests combined page payloads and fans them out from browser-side stores, preventing one warehouse request per chart.
10. A separate PySpark path exports one immutable five-file source batch. It publishes Bronze and curated Parquet through deterministic quality gates. It records benchmark evidence and creates offline clustering artifacts.

### 3.2 Architectural decisions and tradeoffs

#### Snowflake as analytical source of truth

**Why:** Marketplace sharing avoids copying provider-owned raw data, and SQL window, normalization, and pattern operations run close to managed compute. Live profiling showed that repeatedly compiling the layered extended views, rather than scanning or spilling, dominated detailed requests. The extended serving path therefore materializes deterministic refresh-time transformations while preserving `COVID_ENRICHED_EXTENDED` and `CASE_INCREASE_PATTERNS_EXTENDED` as stable compatibility views. **Tradeoff:** publication owns more work and storage, and source-splice or denominator-policy changes must rerun `sql/09_create_jhu_extension.sql`. The legacy ECDC path remains unchanged, and result hashes protect the materialization boundary from semantic drift.

#### ISO-first integration with explicit exceptions

**Why:** ISO identifiers are more stable than human country names, while a small mapping table makes known exceptions reviewable. **Tradeoff:** no reference mapping is permanently complete. The join-status column surfaces `MATCHED`, `SOURCE_UNAVAILABLE`, `NO_ISO_CODE`, and `UNMATCHED` instead of hiding uncertainty.

#### Preserve negative source corrections

**Why:** negative daily values are legitimate provider revisions. Replacing them would silently rewrite history. **Tradeoff:** cumulative curves can fall and simple pattern or forecasting logic can be sensitive to corrections. The mart exposes flags, and forecasting retains the raw history while flooring only the model's working copy.

#### Versioned WDI publication and frozen denominator

**Why:** Source observations, application policy, and analytical timing answer different questions. The immutable WDI history preserves source fidelity. A registry identifies the selected snapshot.

The 2019 mart provides a pre-pandemic baseline. The frozen denominator prevents a routine WDI revision from silently changing COVID rates.

**Tradeoff:** Publication requires two controlled transactions, reconciliation, snapshot-aware caching, and a separate denominator approval lifecycle. These controls add code but remove a hidden semantic dependency.

#### MongoDB for annotations

**Why:** Comments are user-generated, semi-structured context with a lifecycle independent of the analytical mart.

**Tradeoff:** Snowflake validation and MongoDB insertion cannot share a transaction. The service boundary manages consistency.

#### Redis as a budget gate

**Why:** on a credit-limited Snowflake trial, silently bypassing an unavailable cache can turn a dependency outage into repeated paid queries. **Tradeoff:** analytical availability depends on Redis. The decision is deliberate and visible through `503 cache_unavailable` responses.

#### Combined dashboard payloads

**Why:** one page request makes latency, caching, and warehouse cost predictable. **Tradeoff:** payloads can contain more data than a single chart needs. At the current scale, this is cheaper than network and query fan-out.

#### Spark isolated from the serving path

**Why:** Spark is mandatory in the engineering brief but unnecessary for a 61,900-row interactive workload. Isolation shows big-data engineering without adding Java or model latency to the API.

**Tradeoff:** The ECDC benchmark transformations need semantic-equivalence controls. Clustering consumes the governed extended mart and avoids a third splice implementation.

#### Transparent forecast candidates

**Why:** the source is short, historical, corrected, and non-stationary. A 7-day mean and bounded recent trend are easier to audit than a complex model and require no heavy runtime dependency. **Tradeoff:** the models cannot represent interventions, seasonality beyond the weekly mean, or structural breaks. Their output is explicitly a workflow demonstration.

## 4. Snowflake setup and data exploration

The documented account path uses AWS Europe (Stockholm). `sql/00_project_setup.sql` creates the normal five-credit bootcamp resource monitor.

The monitor sends a notification at 50%. It suspends the warehouse at 80% and suspends it immediately at 100%.

Setup reasserts `COVID_WH` as X-Small Gen2. It also reasserts 60-second auto-suspend, auto-resume, monitor attachment, and disabled Query Acceleration.

The controlled audit temporarily used 25 credits. The repository does not use this higher value as the default.

`COVID_PROJECT_ADMIN` owns deployment work. `COVID_APP_ROLE` receives only warehouse usage and mart read access.

The exploration SQL inventories Marketplace objects and columns before encoding assumptions. It checks date coverage, duplicate country-date rows, null percentages, and negative daily values. The key semantic result is that `CASES` and `DEATHS` are daily measures rather than cumulative totals. Cumulative values are therefore calculated with ordered window sums, not by subtracting adjacent source rows.

The legacy ECDC snapshot from 26 July 2026 contained 61,900 daily rows across 214 locations. Its coverage was 31 December 2019 through 14 December 2020.

The extended mart from 31 July contains 224,265 rows and 221 ISO3 countries. Coverage continues through 9 March 2023.

The mart has zero duplicate location and date keys. A total of 4,194 rows classify an unavailable COVID rate denominator.

These values describe the deployed snapshot. They do not describe all future Marketplace refreshes.

Automated EDA has two levels. `scripts/run_eda.py` exports coverage, missing population, correction, and latest-country CSVs from the Snowflake mart. The PySpark entry point adds explicit-schema profiling, null and distinct counts, numeric summaries, bounds, duplicate checks, schema-drift detection, and machine-readable quality publication.

## 5. Data enhancement and analytical model

The committed WDI snapshot contains 3,255 observations. It covers 217 non-aggregate economies, five indicators, and three years.

The 29 July 2026 manifest records 102 null indicator values. It has no duplicate candidate keys or identity conflicts.

The source last-update date is 13 July 2026. Minimum coverage was 100% for population and population aged 65+.

Coverage was 99.54% for density and 95.85% for real GDP per capita. PPP health expenditure coverage was 88.94%.

Each indicator exceeded its publication threshold.

The project selects five distinct analytical variables. They are `SP.POP.TOTL`, `EN.POP.DNST`, `SP.POP.65UP.TO.ZS`, `NY.GDP.PCAP.KD`, and `SH.XPD.CHEX.PP.CD`.

The project labels real GDP per capita in constant 2015 US dollars. It does not call this variable total GDP or pandemic impact.

The project uses this label for health expenditure:

- Current health expenditure per capita, PPP — current international $, 2019.

The project does not calculate a change for this current-price series.

The 2019 values provide country context measured before the pandemic. The project retains the 2020 and 2021 real-GDP-per-capita values to describe later change.

The names `REAL_GDP_PER_CAPITA_CHANGE_*` avoid causal “impact” language. Separate fields expose the active WDI population and the frozen COVID rate denominator.

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
| `STAGING.JHU_COUNTRY_DAILY` | Transient table | Normalized JHU continuation at the same country/date grain |
| `STAGING.COVID_COUNTRY_DAILY_EXTENDED` | View | Deterministic ECDC/JHU splice under the reviewed handoff policy |
| `MARTS.COVID_ENRICHED_EXTENDED_DATA` | Transient table | Refresh-time extended enrichment and cumulative windows |
| `MARTS.COVID_ENRICHED_EXTENDED` | View | Stable public projection over the materialized extended rows |
| `MARTS.CASE_INCREASE_PATTERNS_EXTENDED_DATA` | Transient table | Refresh-time segmented pattern recognition |
| `MARTS.CASE_INCREASE_PATTERNS_EXTENDED` | View | Stable public projection over materialized patterns |
| `MARTS.COUNTRY_LATEST_METRICS_EXTENDED` | Transient table | Extended overview and canonical identity lookup snapshot |

`NULLIF` prevents division by zero. The model keeps missing observations separate from zero.

Per-capita metrics use the frozen denominator. Mortality uses cumulative deaths divided by cumulative confirmed cases.

These are reported-data indicators, not estimates of infections or infection fatality.

Live Snowflake verification accepted active snapshot `wdi2-2019-2021-372906f371e0391f` with 3,255 observations. It found 203 frozen-denominator countries and 213 context-eligible baseline rows.

The snapshot contains 639 annual rows, 213 context rows, and zero duplicate observation keys. Migration reconciliation compared 61,836 canonical ISO3 and date rows.

No keys were missing. All case, death, and denominator values matched exactly. Each per-capita and mortality difference was zero.

The evidence reports 64 old and 64 new rows without canonical ISO3 separately. It does not force these rows into a name join.

The denominator planner compared all 203 frozen countries and 59,336 covered COVID rows to the active WDI population. It found zero population or rate differences.

The planner correctly left the proposed `v2` plan unapproved. A source refresh cannot create a denominator version without a policy change.

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

The API resolves the submitted country against Snowflake. It verifies that the date exists before it inserts the annotation.

Canonical identity fields avoid separate comments for `LV`, `LVA`, and `Latvia`. The API normalizes dates to UTC midnight.

The API trims comments and limits them to 1,000 characters. It limits creator names to 80 characters.

Two non-unique compound indexes support chronological country and date reads. They also support metric-filtered date ranges.

The indexes are non-unique so multiple users can discuss the same point. A public product would require authentication, moderation, pagination, edit and delete operations, and database validation.

## 7. API implementation and caching

FastAPI exposes liveness, readiness, and an explicit Snowflake health check. It also exposes country, comparison, dashboard, forecast, pattern, and annotation operations.

Swagger documentation is available at `/docs`.

`GET /countries/{identifier}/context` returns metadata-rich baseline, annual and change fields. Each indicator carries value, missing status, year, unit, code and snapshot ID. The combined Country Explorer response carries the same object without another browser request. The methodology is explicitly `descriptive` and warns that pandemic-period changes do not establish causality.

Pydantic request and response contracts validate API data. Enum-based metric selection and bind parameters constrain user input.

The API uses a least-privilege Snowflake role, sanitized dependency errors, and structured request IDs. Dashboard settings contain no credential fields.

The repository interpolates metric names only after an enum-to-column allowlist lookup.

The forecast endpoint is:

```text
GET /forecast?country=LV&metric=new_cases&days=30&lookback_days=90
```

It accepts daily cases or deaths, a 1-30-day horizon, and a 42-180-observation window. Snowflake applies the history limit before data crosses the network. The response includes reported history, predictions, lower and upper bounds, both candidates' MAE/RMSE, the selected model, and caveats.

Redis uses versioned canonical keys. The service validates cached JSON against the declared Pydantic model.

Every COVID-derived key includes `COVID_DATASET`. WDI-only context keys remain snapshot-based.

Comparison and combined-page keys include both identities. A dataset cutover or WDI publication cannot reuse an ambiguous response.

Stable analytical payloads use a 24-hour default. Forecasts use six hours.

A 60-second per-key lease prevents duplicate Snowflake queries from concurrent misses. Waiters stop after 15 seconds.

Prefix-scoped invalidation uses incremental `SCAN`, not database-wide `FLUSHDB`.

The application loads the committed manifest at startup. If it differs from the active snapshot, only the context endpoint returns `503 context_data_unavailable`.

If the application cannot read the manifest, combined-page keys use the revision `unavailable`. This value keeps COVID-only responses separate from verified context.

COVID summary, time-series, and forecast routes remain available. Country Explorer shows epidemiological content with a context warning.

This failure boundary prevents an optional context failure from stopping the platform.

Snowflake sessions use a 10-second login timeout and a 30-second network timeout. Each statement also has a 30-second timeout.

Query tags use `<application-prefix>:<dataset>:<repository-operation>`. Success logs contain the operation, query ID, timings, row count, and selected dataset.

Logs exclude SQL text, parameters, credentials, and connector URLs.

## 8. Dashboard implementation

The Dash application provides:

1. **Status:** Runs low-cost API, MongoDB, and Redis checks. Only an explicit button queries Snowflake.
2. **Overview:** global KPIs, top-ten case/death bars, and cases-per-100,000 choropleth.
3. **Country Explorer:** Provides country, metric, and date controls. It shows COVID charts, the rate denominator, baseline cards, and descriptive GDP changes.
4. **Comparison:** two to ten countries across cases per 100,000, deaths per 100,000, and mortality, followed by a five-metric WDI baseline comparison.
5. **Forecast:** model choice, holdout MAE/RMSE, history, 1-30-day forecast, and empirical interval.
6. **Increase Patterns:** sustained daily case-increase sequences with date and duration filters.
7. **Annotations:** create and filter comments stored in MongoDB.

Country Explorer shows COVID outcomes first. COVID KPIs precede a compact five-card WDI baseline row.

The four primary COVID charts precede the larger GDP panel. The GDP chart includes zero in its value scale.

Signed two-decimal badges show the change. This design prevents three annual observations from exaggerating small movements.

Cards use fixed precision for each unit. They show missing source observations as unavailable, not zero.

The WDI section uses context from the combined Country Explorer payload. It adds no browser request or Snowflake statement.

Population, density, age, and health expenditure remain baseline cards. Only real GDP per capita has public annual history for 2019-2021.

One section footer identifies the active WDI snapshot. Cards do not repeat operational metadata.

Comparison also shows COVID analysis first. Three epidemiological charts precede the WDI controls and charts.

The WDI chart includes zero in its value scale. This scale prevents small cross-country differences from appearing larger than they are.

The matrix shows all five baseline values with fixed precision. It uses whole population, one-decimal density, one-decimal age share, and whole currency units.

Missing source observations remain `Not available`. The application does not convert them to zero.

WDI population is context, not the frozen COVID rate denominator. The section omits GDP changes and annual histories.

It shows the WDI snapshot footer once.

The Comparison API adds optional baseline context to each country series. One parameterized statement returns COVID observations and the one-row-per-ISO3 baseline.

The join repeats baseline columns across the daily result. It avoids up to ten extra warehouse queries and preserves one browser request.

The service validates each joined row against the committed WDI snapshot. Missing or mismatched context affects only that country.

COVID results remain available. `countries_without_data` still refers only to missing COVID observations.

Cache contract version 2 includes the committed snapshot ID. Responses cannot cross WDI publications.

Selector changes render from the page store. They do not call the API or Snowflake again.

Responsive Mantine components, loading overlays, empty states, error alerts, accessible status roles, and a compact sidebar support usability. Page-level `dcc.Store` objects prevent repeat API calls from render callbacks.

The dashboard still needs source and method text on each chart. It also needs download controls for reproducible offline analysis.

## 9. Forecasting methodology and result

The model copy uses non-negative incident counts. Returned history retains negative source corrections.

The 7-day candidate uses the mean of the most recent week. The trend uses at most the latest 42 observations and actual date offsets.

This limit reduces the effect of an early-pandemic regime. Actual date offsets avoid false zero values for missing dates.

The final 14 observations form a rolling-origin, one-step-ahead holdout. At each validation date, both candidates use only earlier data.

The project rejects random train and test splits because they leak future regimes into past predictions. The candidate with lower MAE wins.

An exact tie selects the simpler weekly mean. The response also reports RMSE so large misses remain visible.

The legacy ECDC-only verification ran on 29 July 2026. Latvia's latest 90 daily case observations covered 16 September through 14 December 2020.

The 7-day mean won with MAE 160.827 and RMSE 214.183. The trend scored MAE 173.407 and RMSE 225.542.

The first forecast was 623.143 cases for 15 December 2020. Its empirical band was 210.429 to 1,035.857.

The 30th point remained 623.143 with a band of 0 to 1,559.091. This is a reproducible model example, not an extended-dataset coverage claim.

The interval uses the larger of the selected model's holdout RMSE and nearest-rank 90th-percentile absolute error, widened by the square root of forecast horizon. It is descriptive, not a calibrated probabilistic confidence interval. The API caveat now reports the actual final historical date returned by the selected Snowflake dataset instead of hardcoding 2020. Even with extended coverage through March 2023, no forecast in this project is current public-health guidance.

### 9.1 Offline clustering bonus

Snowflake remains responsible for the governed ECDC-to-JHU cutover. Spark uses `COVID_ANALYTICS.MARTS.COVID_ENRICHED_EXTENDED` as a fifth immutable input for clustering only.

The original ECDC input remains the transformation and benchmark fact. This design prevents a second implementation of the splice boundary.

ISO3 is the analytical unit. Eligibility requires a positive governed population denominator, complete normalized measures, and at least 180 distinct daily observations.

The evidence retains each exclusion reason. Source and Bronze data retain negative corrections.

The model copy floors incident rates at zero. Complete calendar-based 14-day windows then produce five COVID-only features:

- Latest cumulative cases per 100,000.
- Latest cumulative deaths per 100,000.
- Peak 14-day mean cases per 100,000.
- Peak 14-day mean deaths per 100,000.
- Volatility of the 14-day mean case rate.

The pipeline applies `log1p` and standardization to the five non-negative features before Spark ML KMeans. WDI variables do not enter the feature vector.

WDI variables join after fitting to create descriptive local profiles. This design does not imply that WDI variables caused cluster membership.

Model selection evaluates `k=2..6` across seeds 13, 29, 47, 71, and 97. It rejects a seed run when any cluster is below `max(3, 2% of eligible countries)`.

Publication requires four valid seeds, a positive median silhouette, and a median pairwise Adjusted Rand Index of at least 0.75. The highest median silhouette wins.

Candidates within 0.01 use the smaller `k`. The authoritative seed is closest to the candidate median.

A lower seed breaks a tie. The pipeline relabels predictions by ascending standardized centroid burden.

Immutable ignored output contains assignments, raw/log/standardized features, centroid distance, exclusions, WDI profiles, fitted scaler, and selected KMeans model. Publishable diagnostics contain aggregate eligibility, candidate, stability, cluster-size, assignment-checksum, runtime, skew, spill, and lineage evidence only. Country assignments, daily rows, and WDI cluster profiles are not committed.

The implementation passed fixture validation in the pinned runtime. This worktree had no credentials or checksum-verified extended source batch.

The report does not interpret country-level results or claim version 4 evidence. The model segments historical reporting outcomes.

It does not identify causal, policy, or epidemiological regimes.

## 10. Pattern recognition

Controlled publication uses `MATCH_RECOGNIZE` to build `CASE_INCREASE_PATTERNS_EXTENDED_DATA`. A pattern starts with one day and needs at least three consecutive increases.

Each daily value must exceed the previous daily value. A one-day date difference prevents a gap from appearing as an uninterrupted run.

`CASE_INCREASE_PATTERNS_EXTENDED` remains the stable public view. The legacy ECDC object remains unchanged.

The extended snapshot produced 5,135 matches across 193 locations, with zero invalid durations and zero matches below the required three increases. The prior legacy snapshot produced 1,769 matches across 170 locations. These are reproducible reporting patterns. They do not prove transmission mechanisms, policy effects, or clinical severity.

## 11. Performance optimization

### 11.1 Snowflake and API path

The review separated client-observed duration from Snowflake elapsed, compilation, and execution time. The measurement disabled result-cache reuse.

Pre-change detailed paths had 1,471–2,350 ms median Snowflake elapsed time. Compilation used 1,057–1,832 ms of this time.

Measured queries scanned about 7.1–7.2 MB. They had zero overload queue, provisioning queue, QAS bytes, or spill.

The project removes repeated logical expansion. It does not buy more scan capacity.

`COVID_ENRICHED_EXTENDED_DATA` materializes the ECDC/JHU splice, denominator and WDI enrichment, and window calculations. It is a transient refresh table.

`CASE_INCREASE_PATTERNS_EXTENDED_DATA` materializes the segmented `MATCH_RECOGNIZE` result. Established public names remain compatibility views.

Repository object names, routes, and response contracts do not change. `sql/09_create_jhu_extension.sql` owns both rebuilds and rebuilds `COUNTRY_LATEST_METRICS_EXTENDED`.

The legacy ECDC objects remain unchanged.

The live comparison used one warmup and five measured repetitions for six representative repository operations. Each statement had a run-specific operation tag and query ID.

The capture process recorded operator statistics. Detailed medians changed as follows:

| Operation | Snowflake before | Snowflake after | Compilation before | Compilation after | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| Time series | 1,471 ms | 149 ms | 1,178 ms | 117 ms | 89.9% lower elapsed |
| Country dashboard | 1,607 ms | 470 ms | 1,134 ms | 284 ms | 70.8% lower elapsed |
| Comparison | 1,582 ms | 464 ms | 1,057 ms | 310 ms | 70.7% lower elapsed |
| Patterns | 2,350 ms | 233 ms | 1,832 ms | 189 ms | 90.1% lower elapsed |
| Forecast history | 1,438 ms | 158 ms | 1,118 ms | 124 ms | 89.0% lower elapsed |

All detailed paths passed the keep threshold. None regressed.

`COUNTRY_LATEST_METRICS_EXTENDED` already materialized the overview. Snowflake elapsed time changed from 106 ms to 118 ms outside the changed boundary.

Client duration decreased from 759.1 ms to 474.1 ms. Public views and materialized tables have equal row counts and unordered hashes.

They contain 224,265 enriched rows, 5,135 patterns, and 222 latest-country rows. The extended mart retains 221 ISO3 countries.

Coverage is 31 December 2019 through 9 March 2023. The mart has zero duplicate keys and the same 4,194 null-denominator classifications.

Post-change detailed scans remain below 9 MB. The materialized enriched table uses 8.43 MB across eight micro-partitions.

Some enriched query byte counts increased while compilation and execution time decreased. This result identifies logical-plan work as the limiting factor.

A clustering key, automatic clustering, materialized view service, or Search Optimization would add cost without a measured latency need. Reassess these features after material scale or service changes.

Setup reasserts the warehouse contract on each run. The contract uses X-Small Gen2, 60-second auto-suspend, auto-resume, an attached monitor, and disabled QAS.

QAS used zero accelerated bytes in the controlled pre-change run. It used zero credits in the preceding seven-day history.

Snowflake bills QAS separately from warehouse compute. Resource monitors control warehouses, not serverless features.

Disabled QAS gives the project a clear cost boundary. The repository keeps a five-credit monthly quota. The live audit temporarily used 25 credits.

See the [QAS](https://docs.snowflake.com/en/user-guide/query-acceleration-service) and [resource-monitor](https://docs.snowflake.com/en/user-guide/resource-monitors) documentation.

API sessions use a 10-second login timeout, 30-second network timeout, and 30-second statement timeout. Query tags include the application, dataset, and repository operation.

Sanitized logs include query ID, operation, timings, returned rows, and dataset. They exclude SQL, bind values, credentials, and URLs.

Post-change connection medians were 223.7–292.3 ms. Connection time is now a visible part of an uncached request.

Redis limits cache-miss frequency. No evidence shows sustained miss concurrency that justifies connection pooling and its session risks.

Every COVID-derived cache key includes `COVID_DATASET`. WDI-only context keys remain snapshot-based.

Combined pages use the revision `unavailable` when the application cannot read the optional manifest. The 60-second lease covers the statement bound.

The 15-second waiter stops request threads before the lease ends. After validation, the prefix-scoped cache clear removed one project key.

The [Snowflake evidence report](snowflake/optimization_evidence_2026-07-31.md) contains sanitized results. The [JSON evidence](snowflake/performance_evidence.json) contains per-query data.

Immediate measurements use Information Schema query history. Snowflake documents up to 45 minutes of delay for Account Usage `QUERY_HISTORY`.

Resource-monitor and warehouse credit totals are aggregate values. They cannot identify API traffic alone. Query tags identify the measured statements.

See the [QUERY_HISTORY reference](https://docs.snowflake.com/en/sql-reference/account-usage/query_history) and [Python Connector guidance](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-connect).

The extension script rebuilds `COUNTRY_LATEST_METRICS_EXTENDED` after it switches the verified compatibility view. Overview and identity access remain materialized.

WDI publication uses separate controlled activation and downstream verification. This process preserves the last-known-good state.

### 11.2 Spark optimization evidence

The pipeline generated evidence version 3 on 30 July 2026 in the rebuilt pinned image. The image uses Python 3.12.13, PySpark 3.5.6, and Java 17.0.19.

The run used `local[2]`, adaptive execution, and 32 shuffle partitions. It used immutable source batch `wdi-context-qa-v1`.

The batch contains 61,900 ECDC rows and 217 frozen population rows. It also contains 14 mapping rows and 3,255 WDI observations.

The WDI snapshot is `wdi2-2019-2021-372906f371e0391f`. All four file checksums and the combined batch checksum passed before Spark initialization.

That committed evidence predates the extended fifth input, runtime hardening, and clustering stage. It remains the accepted real-data record and was deliberately not rewritten by synthetic fixtures. Version 4 requires a new credentialed export whose extended entry records selected Snowflake object, row count, date range, country count, byte count, and checksum.

Spark derives the same context-eligible country universe as Snowflake before it joins the WDI baseline. This rule retains valid ISO3 countries without WDI observations.

The rule prevents the comparison from shrinking to matched rows only. The projection matched the accepted Snowflake artifact exactly.

It contains 213 rows and 20,199 canonical bytes. Its SHA-256 is `6fa8fc8208d748a8dfbd2b4d606eb09cf2faae59869dfa52c62f3b3913872d83`.

The optimized plan recorded three build-right broadcast hash joins. They join country mapping, frozen population, and the narrow WDI baseline.

| Comparison | Baseline median | Candidate median | Result | Engineering conclusion |
| --- | ---: | ---: | --- | --- |
| Early projection/filter | 183.939 ms | 216.075 ms | Candidate was not faster | Keep projection for contract discipline and reduced downstream width, not as a local-speed claim |
| Three broadcast joins | 762.517 ms | 561.944 ms | Candidate was faster | The historical run supports the three broadcasts at captured sizes. The runtime gates each hint from manifest bytes and validates the plan. |
| Adaptive duplicate aggregation | 347.056 ms | 426.586 ms | Candidate was not faster | AQE remains a scale-safety setting. This fixture does not show a speed increase. |
| Reused-frame cache | 323.322 ms | 372.224 ms | Candidate was not faster | Cache only when reuse, recomputation cost, and memory pressure justify it. This run does not justify caching. |
| File layout | 2,787.065 ms | 1,645.820 ms | Candidate was faster | One output file avoids small-file overhead at this measured size |

Every comparison passed schema, row-count, and row-multiset checksum gates before timing. Each variant used one warmup and five measured repetitions.

Only broadcast joins and file layout were faster in this run. Early projection, AQE, and caching were slower and are not speed claims.

The version 3 calibration measured 1,891,286 bytes. Thirteen estimated monthly partitions had a median of 191,542 bytes.

This value was far below the 128 MiB target. The pipeline published one unpartitioned 421,412-byte Parquet file.

The new implementation writes temporary month-partitioned Parquet. It measures each compressed directory before it selects the final layout.

The report does not claim a real-data result for this change before version 4 publication.

One module owns the runtime policy. It derives bounded shuffle partitions from source bytes and advisory partition size and records each override.

The runtime broadcasts mapping, population, and WDI dimensions only below a configurable manifest-size limit. Benchmarks disable automatic broadcast to keep physical-plan assertions deterministic.

Bronze and curated file counts use byte thresholds. The job coalesces to reduce partitions and repartitions to increase them.

Country-key skew evidence records median, p95, maximum rows per key, maximum share, and maximum-to-median ratio. A ratio above 5 creates a warning.

One key above 10% also creates a warning. The design does not add salting without measured task imbalance.

Event-log summaries include input bytes, shuffle bytes, spill, peak execution memory, executor runtime, and JVM garbage-collection time.

The evidence path combines joined-row and unmatched-location metrics in one post-benchmark aggregate. It reuses the 213-row fingerprint input to derive snapshot IDs.

This design removes redundant Spark actions without changing timed variants. The path does not cache the enriched frame.

Pre-materialization would warm the file-layout input and weaken comparison isolation. It would also conflict with the measured cache tradeoff.

Explicit schemas and header inspection fail before Bronze publication on drift. The WDI schema uses fixed decimal precision and the canonical `\N` null token.

The extended contract also gates duplicate country-dates, missing identity, invalid denominators, null model measures, insufficient history, and inconsistent source segments. Corrupt rows and quality failures remain run-local evidence.

Immutable source, snapshot, ingestion, benchmark, and model IDs prevent silent overwrite.

Fixture runs publish ignored preview evidence only. A real `snowflake_export` run may atomically publish version 4 evidence and clustering diagnostics after quality, correctness, physical-plan, eligibility, minimum-cluster-size, silhouette, and stability gates all pass. Publishable clustering diagnostics contain no country assignments or WDI profiles. A failed model-selection or publication gate preserves the accepted version 3 evidence.

The quality status was `WARN`, not `FAIL`. A total of 339 ECDC rows lacked a source ISO value.

The source also contained 18 negative case corrections and 8 negative death corrections. Policy classifies these values as expected corrections.

Each fail-severity gate passed, and curated publication succeeded. Atomic publication replaced the evidence only after all validation gates passed.

The key lesson is that Spark optimization is workload-specific. Version 3 supports broadcast joins and consolidated file layout at the captured scale.

It does not support projection, AQE, or caching as speed claims. Fixture data validates the new controls and offline model.

These results are not real-data or distributed-scale claims. Snowflake SQL remains the production semantic and serving path.

## 12. COVID-19 insights

The findings below describe the legacy ECDC snapshot that ends on 14 December 2020. They provide reproducible historical analysis.

They do not describe the coverage of the promoted extended dataset.

1. Latest-location totals were 71,503,614 confirmed cases and 1,612,833 deaths, a reported cumulative case-fatality ratio of 2.2556%. Because each location contributes its own latest available row, the snapshot is not perfectly synchronized.
2. Cases per 100,000 were highest for Andorra (9,483.1), Montenegro (6,611.3), and Luxembourg (6,546.8). Small denominators make normalized burden very different from absolute burden.
3. Deaths per 100,000 were highest for Belgium (155.6), San Marino (146.7), and North Macedonia (114.3). Raw totals can hide this per-capita severity.
4. Among locations with at least 10,000 cases, reported mortality was highest for Mexico (9.1159%), Ecuador (6.8651%), and Sudan (6.2985%). Testing access, outcome lag, and attribution differences prevent a causal ranking of healthcare quality.
5. Lithuania recorded 3,381.1 cases per 100,000. Estonia recorded 1,358.0, and Latvia recorded 1,351.0. Reported mortality ranked Latvia first at 1.3593%, then Lithuania at 0.8682%, and Estonia at 0.8253%.
6. Eighteen negative-case rows and eight negative-death rows occurred across 17 locations. Correction handling is therefore materially important, not a theoretical edge case.
7. The legacy snapshot contains 1,769 sustained-increase patterns across 170 locations. These patterns are reporting sequences, not epidemiological regimes. The extended snapshot contains 5,135 patterns across 193 locations.

Confirmed cases are not infections. Countries differed in testing availability, reporting definitions, weekend effects, backfills, and death attribution. These caveats apply to charts, rankings, patterns, and forecasts.

## 13. Code quality, testing, and deployment

The codebase separates API routes, contracts, services, repositories, dashboard components, Spark transformations, SQL, setup scripts, and tests. Comments explain decisions that are not visible from syntax.

These decisions include cache failure policy, immutable publication, source corrections, atomic swaps, temporal validation, and empirical intervals. They also include bounded history, broadcasts, persistence, and file layout.

`pyproject.toml` declares dependencies, and `uv.lock` records resolved versions. Package metadata declares Python 3.12 patch compatibility.

`.python-version` and Docker pin Python 3.12.13. Java and PySpark exist only in the optional Spark image and dependency group.

GitHub Actions runs lock verification, isort, Black, Ruff, the application suite, and the Spark fixture suite.

Verification through 31 July 2026:

| Check | Result |
| --- | --- |
| Previously accepted application/unit/contract suite | 101 passed. This is unchanged historical review evidence. |
| Changed-path export, event-metric, schema, and publication tests | 14 passed in this worktree |
| Ruff | Passed |
| isort and Black | Passed |
| Compose configuration | Parsed successfully |
| Dated Snowflake WDI publication and mart verification | Passed. Evidence is in `reports/world_bank/snowflake_verification.json`. |
| Dated legacy/new COVID reconciliation | Passed. It found 61,836 exact canonical rows and zero tolerance failures. |
| Dated context/API smoke | Passed with `COVID_APP_ROLE`. Combined page context was available. |
| Tagged Snowflake before/after profile | Passed. Each phase contains 36 statements and equal hashes. Detailed-query medians decreased by 70.7% to 90.1%. |
| Snowflake context export fingerprint | Passed. The immutable batch records 213 baseline rows. |
| Pinned Docker Spark image | The previous build passed. Current dependencies are unchanged. |
| Exact pinned Spark runtime suite | 31 passed with Python 3.12.13, PySpark 3.5.6, and Java 17.0.19 |
| Committed Spark evidence | Version 3. Four source checksums, five correctness gates, three broadcasts, the cross-engine match, and publication passed. |
| Extended clustering evidence | Implementation and fixture checks passed. The credentialed five-file version 4 run is pending. |

The normal setup path requires Docker and a Snowflake account. `setup.ps1` or `setup.sh` starts the containerized bootstrap.

The bootstrap validates prerequisites and postconditions. It publishes the committed WDI snapshot without a network request.

It preserves or seeds the frozen denominator and deploys the marts. It also starts dependencies, creates MongoDB indexes, and runs smoke checks.

The documentation includes manual recovery instructions.

## 14. Limitations and prioritized next work

Before submission:

1. Confirm the spelling of the student name.
2. Commit all working-tree changes.
3. Push `extended_0.8`.
4. Replace the reviewed merge reference with the final hash.
5. Run the documented setup on a clean virtual machine.
6. Capture healthy API, annotation, forecast, and dashboard evidence.
7. Run the credentialed five-file Spark export and pipeline.
8. If all quality and publication gates pass, publish version 4.
9. Retain the final CI link and Spark evidence artifacts with the submitted commit.

Engineering follow-ups:

1. Add a production Compose override without bind mounts or reload.
2. Add TLS and secret injection.
3. Add authentication and rate limiting.
4. Add a MongoDB JSON Schema validator.
5. Add integration tests with disposable Redis and MongoDB containers.
6. If scale or service objectives change, revalidate the materialization boundary and connection policy.
7. Add chart-level source notes, correction markers, downloads, and synchronized-date options.
8. Do not interpret fixture segments or treat descriptive WDI profiles as causal evidence.
9. Reassess clustering features and thresholds only from real extended-data diagnostics.

## 15. Conclusion

The project is a defensible end-to-end data engineering submission. It combines a managed warehouse, external data, quality controls, a cost-aware API, and operational NoSQL data.

It also includes interactive visualization, forecasting, pattern recognition, and measured Spark engineering. The report documents tradeoffs and negative benchmark results directly.

The remaining submission risk concerns operational evidence, not missing core functionality. Push the final working tree and run the complete stack on a clean VM.

A credentialed immutable extended batch must produce version 4. Until then, do not present the clustering bonus as a real-data result.

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
