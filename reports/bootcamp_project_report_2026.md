# COVID-19 Data Integration, Analysis, and Visualization Platform

Implementation report

**Student:** Nestor Kulik  
**Date:** 29 July 2026  
**Repository:** https://github.com/synku20777/bootcamp-project  
**Reviewed branch and commit:** `api-works`, base commit `84f8376` plus this implementation working tree

## 1. Executive summary

This project integrates the free Snowflake Marketplace COVID-19 Epidemiological Data share with a checksum-verified World Bank 2020 population snapshot. Snowflake owns the analytical truth, PySpark demonstrates an immutable Bronze and profiling path, FastAPI exposes typed analytical and forecasting contracts, Redis protects the Snowflake trial budget, MongoDB stores user annotations, and Dash provides six interactive pages.

The implementation now covers every required in-repository functional task. Forecasting was the final missing requirement and is implemented as a transparent comparison between a 7-day mean and a recent linear trend. The selected model is chosen by rolling temporal holdout MAE, reports both MAE and RMSE, and returns a descriptive 90% empirical error band. Clustering remains unimplemented because it is explicitly a bonus item. Final submission publication and clean-environment acceptance evidence remain release steps rather than code gaps.

The strongest engineering qualities are reproducibility, explicit data contracts, least-privilege access, source-correction fidelity, bounded warehouse queries, fail-closed cache protection, and unusually careful Spark evidence. The project does not claim Spark is faster at this data volume: three common “optimizations” measured slower, while caching a reused frame and writing one appropriately sized Parquet file measured faster.

At review time, 77 application, API, dashboard, repository, ingestion, and forecasting tests passed. Ruff and Black passed. Docker Compose configuration parsed successfully. A fresh Spark test run was not completed on the review host because Java was not installed and Docker Desktop was stopped; the committed Spark evidence is therefore reported as previously generated evidence rather than a newly reproduced benchmark. A final submission should commit and push the working tree and perform one clean-VM acceptance run.

## 2. Requirement compliance

| Assignment task | Status | Implementation evidence | Qualification |
| --- | --- | --- | --- |
| 1. Marketplace data and resource monitor | Complete | Imported ECDC source contract; AWS Stockholm setup instructions; 5-credit monthly monitor; X-Small warehouse; 60-second auto-suspend | Marketplace installation remains a manual account action because it requires the student's Snowflake account acceptance |
| 2. Exploration and enhancement | Complete | Reusable SQL EDA, automated CSV exports, explicit Spark profiling, World Bank population integration, normalized per-capita and mortality metrics | The external source is population only; GDP and median age were optional examples, not requirements |
| 3. NoSQL model | Complete | MongoDB annotations with Pydantic validation, canonical analytical identity, UTC dates, and two compound indexes | No database-side JSON Schema validator; API validation is authoritative |
| 4. Python API | Complete | FastAPI queries Snowflake, reads/writes MongoDB, performs on-the-fly metrics and forecasting, and returns typed JSON | No public authentication or rate limiting |
| 5. Interactive visualization | Complete | Dash pages for status, overview, country exploration, comparison, forecasting, and annotations | Browser QA should be repeated on the final clean VM |
| 6. Time-series forecasting | Complete | 7-day mean versus recent linear trend, rolling holdout, MAE/RMSE, 1-30-day horizon, empirical interval, API and dashboard | This is an interpretable baseline, not an epidemiological model; clustering is bonus and not implemented |
| 7. Performance optimization | Complete with evidence limitation | Snowflake monitor/auto-suspend, precomputed latest snapshot, projection, bounded date/country filters, one-statement repository budget, consolidated page payloads | No newly captured Snowflake Query Profile comparison in this review |
| 8. API caching | Complete | Redis TTLs, versioned keys, Pydantic cache revalidation, prefix-scoped invalidation, stampede lock, fail-closed behavior | Redis becomes an intentional availability dependency to protect trial credits |
| 9. Pattern identification | Complete | Snowflake `MATCH_RECOGNIZE` identifies at least three consecutive daily increases | Results are reporting patterns, not causal transmission regimes |
| 10. GitHub and configuration | Partial until final push | GitHub repository, Dockerfiles, Compose, `.env.example`, uv lockfile, CI, setup scripts, tests, and report | The final working-tree changes must be committed and pushed before submission |

## 3. Architecture

### 3.1 End-to-end data flow

1. The Snowflake Marketplace ECDC share provides country-level daily cases and deaths.
2. SQL exploration establishes the real grain, date coverage, null behavior, duplicates, and correction semantics before transformation.
3. A country mapping table resolves known source exceptions. The staging view aggregates duplicate country-date records, creates stable location keys, and derives cumulative measures while preserving negative daily corrections.
4. The World Bank snapshot contributes population and ISO identifiers. Its loader validates the local checksum and schema, loads a uniquely named staging table, validates it in Snowflake, and swaps it atomically into the production name.
5. `COVID_ENRICHED` joins the sources and derives cases and deaths per 100,000, cumulative measures, mortality percentage, join status, and data-correction flags.
6. A small transient latest-country table serves overview and identity resolution. A separate pattern view applies `MATCH_RECOGNIZE` to daily data.
7. FastAPI separates routes, services, typed models, and repositories. Every public Snowflake repository method executes one bounded statement.
8. Redis serves validated analytical responses and blocks cache stampedes. MongoDB owns user-authored annotations.
9. Dash requests combined page payloads and fans them out from browser-side stores, preventing one warehouse request per chart.
10. A separate PySpark path exports one immutable source batch, publishes Bronze and curated Parquet only through deterministic quality gates, and records benchmark evidence.

### 3.2 Architectural decisions and tradeoffs

#### Snowflake as analytical source of truth

**Why:** Marketplace sharing avoids copying provider-owned raw data, and SQL window, normalization, and pattern operations run close to managed compute. **Tradeoff:** the main enriched mart is a view, so general time-series requests recompute transformations. Redis and the latest-country snapshot offset that cost without introducing a second analytical truth.

#### ISO-first integration with explicit exceptions

**Why:** ISO identifiers are more stable than human country names, while a small mapping table makes known exceptions reviewable. **Tradeoff:** no reference mapping is permanently complete. The join-status column surfaces `MATCHED`, `SOURCE_UNAVAILABLE`, `NO_ISO_CODE`, and `UNMATCHED` instead of hiding uncertainty.

#### Preserve negative source corrections

**Why:** negative daily values are legitimate provider revisions. Replacing them would silently rewrite history. **Tradeoff:** cumulative curves can fall and simple pattern or forecasting logic can be sensitive to corrections. The mart exposes flags, and forecasting retains the raw history while flooring only the model's working copy.

#### Atomic population refresh

**Why:** validating a staging table before swap prevents a failed API download or partial write from destroying the last known-good population table. **Tradeoff:** staging, backup, and swap operations add code and require create/rename privileges for the deployment role.

#### MongoDB for annotations

**Why:** comments are user-generated, semi-structured context with a lifecycle independent of the analytical mart. **Tradeoff:** Snowflake validation and MongoDB insertion cannot share a transaction; consistency is managed at the service boundary.

#### Redis as a budget gate

**Why:** on a credit-limited Snowflake trial, silently bypassing an unavailable cache can turn a dependency outage into repeated paid queries. **Tradeoff:** analytical availability depends on Redis. The decision is deliberate and visible through `503 cache_unavailable` responses.

#### Combined dashboard payloads

**Why:** one page request makes latency, caching, and warehouse cost predictable. **Tradeoff:** payloads can contain more data than a single chart needs. At the current scale, this is cheaper than network and query fan-out.

#### Spark isolated from the serving path

**Why:** Spark is mandatory in the engineering brief but unnecessary for a 61,900-row interactive workload. Isolating it demonstrates big-data engineering without adding Java startup latency to the API. **Tradeoff:** there are two transformation implementations whose semantic equivalence must be protected by tests and checksums.

#### Transparent forecast candidates

**Why:** the source is short, historical, corrected, and non-stationary. A 7-day mean and bounded recent trend are easier to audit than a complex model and require no heavy runtime dependency. **Tradeoff:** the models cannot represent interventions, seasonality beyond the weekly mean, or structural breaks. Their output is explicitly a workflow demonstration.

## 4. Snowflake setup and data exploration

The documented account path uses AWS Europe (Stockholm). `sql/00_project_setup.sql` creates a 5-credit monthly resource monitor with notification at 50%, suspension at 80%, and immediate suspension at 100%. `COVID_WH` is X-Small, starts suspended, resumes on demand, and auto-suspends after 60 seconds. `COVID_PROJECT_ADMIN` owns deployment work; `COVID_APP_ROLE` receives only warehouse usage plus read access to the marts.

The exploration SQL inventories Marketplace objects and columns before encoding assumptions. It checks date coverage, duplicate country-date rows, null percentages, and negative daily values. The key semantic result is that `CASES` and `DEATHS` are daily measures rather than cumulative totals. Cumulative values are therefore calculated with ordered window sums, not by subtracting adjacent source rows.

The live snapshot verified on 26 July 2026 contained 61,900 daily rows across 214 locations from 31 December 2019 through 14 December 2020. Population matched 203 locations and 59,336 rows; 11 source-unavailable locations accounted for 2,564 rows. These values are evidence from the deployed snapshot, not universal properties of every future Marketplace refresh.

Automated EDA has two levels. `scripts/run_eda.py` exports coverage, missing population, correction, and latest-country CSVs from the Snowflake mart. The PySpark entry point adds explicit-schema profiling, null and distinct counts, numeric summaries, bounds, duplicate checks, schema-drift detection, and machine-readable quality publication.

## 5. Data enhancement and analytical model

The committed World Bank file contains 217 population records for 2020 and a manifest with a SHA-256 checksum. Snapshot mode makes supervisor deployment independent of the public API; refresh mode is available to developers who want to retrieve the source again.

The analytical model deliberately favors a narrow serving mart over a full star schema because the source already has a simple country-date grain:

| Object | Type | Purpose |
| --- | --- | --- |
| `RAW.WORLD_BANK_POPULATION_2020` | Table | Validated demographic snapshot |
| `STAGING.COUNTRY_CODE_MAPPING` | Table | Explicit source normalization rules |
| `STAGING.COVID_COUNTRY_DAILY` | View | Clean daily grain, stable key, cumulative metrics, correction flags |
| `MARTS.COVID_ENRICHED` | View | Population join, per-capita metrics, mortality, join status |
| `MARTS.COUNTRY_LATEST_METRICS` | Transient table | Small precomputed snapshot for overview and identity lookups |
| `MARTS.CASE_INCREASE_PATTERNS` | View | Sustained daily-increase pattern results |

`NULLIF` prevents division by zero. Per-capita metrics use a common population denominator and mortality uses cumulative deaths divided by cumulative confirmed cases. These are reported-data indicators, not estimates of infections or infection fatality.

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

FastAPI exposes liveness, readiness, an explicit Snowflake health check, overview, country summary, time series, comparison, combined dashboard payloads, forecasts, and annotation create/list operations. Swagger documentation is available at `/docs`.

Security and correctness controls include Pydantic request/response contracts, enum-based metric selection, bind parameters for user values, a least-privilege Snowflake role, sanitized dependency errors, structured request IDs, and no credential fields in dashboard settings. Metric names are interpolated only after an enum-to-column allowlist lookup.

The forecast endpoint is:

```text
GET /forecast?country=LV&metric=new_cases&days=30&lookback_days=90
```

It accepts daily cases or deaths, a 1-30-day horizon, and a 42-180-observation window. Snowflake applies the history limit before data crosses the network. The response includes reported history, predictions, lower and upper bounds, both candidates' MAE/RMSE, the selected model, and caveats.

Redis uses versioned canonical keys and validates cached JSON back into the declared Pydantic model. Stable analytical payloads default to 24 hours; forecasts default to 6 hours. A per-key lease prevents concurrent misses from duplicating a Snowflake query. Prefix-scoped invalidation uses incremental `SCAN`, never database-wide `FLUSHDB`.

## 8. Dashboard implementation

The Dash application provides:

1. **Status:** cheap API, MongoDB, and Redis checks; Snowflake is queried only by an explicit button.
2. **Overview:** global KPIs, top-ten case/death bars, and cases-per-100,000 choropleth.
3. **Country Explorer:** country, metric, and date controls plus selected metric, daily case/death, and mortality charts.
4. **Comparison:** two to ten countries across cases per 100,000, deaths per 100,000, and mortality.
5. **Forecast:** model choice, holdout MAE/RMSE, history, 1-30-day forecast, and empirical interval.
6. **Annotations:** create and filter comments stored in MongoDB.

Responsive Mantine components, loading overlays, empty states, error alerts, accessible status roles, and a compact/mobile sidebar support usability. Page-level `dcc.Store` objects ensure render callbacks do not call the API again. A remaining analytical-UX improvement is explicit source/methodology text on every chart and download controls for reproducible offline analysis.

## 9. Forecasting methodology and result

The modelling copy uses non-negative incident counts; negative source corrections remain in returned history. The 7-day candidate uses the mean of the most recent week. The trend candidate uses ordinary least squares over at most the most recent 42 observations and actual date offsets, which prevents an early-pandemic regime from dominating a recent forecast and avoids inventing zeros for missing dates.

The final 14 observations form a rolling-origin, one-step-ahead holdout. At each validation date, both candidates see only earlier data. Random train/test splitting was rejected because it leaks future regimes into past predictions. The candidate with lower MAE wins; an exact tie selects the simpler weekly mean. RMSE is also reported so large misses remain visible.

For Latvia's latest 90 daily case observations, verified live on 29 July 2026, the history ran from 16 September through 14 December 2020. The 7-day mean won with holdout MAE 160.827 and RMSE 214.183; the trend scored MAE 173.407 and RMSE 225.542. The first forecast was 623.143 cases for 15 December 2020 with an empirical band of 210.429 to 1,035.857. The 30th point remained 623.143 with a widened band of 0 to 1,559.091. The widening is intentionally conservative and demonstrates why long-horizon projections from this baseline should not be over-interpreted.

The interval uses the larger of the selected model's holdout RMSE and nearest-rank 90th-percentile absolute error, widened by the square root of forecast horizon. It is descriptive, not a calibrated probabilistic confidence interval. The data ends in 2020, so no forecast in this project is current public-health guidance.

Clustering was not implemented. It is a bonus task and would be valuable only after defining stable country-level features, scaling them, selecting cluster count, checking stability, and explaining segments without causal overreach.

## 10. Pattern recognition

`CASE_INCREASE_PATTERNS` uses Snowflake `MATCH_RECOGNIZE` to find a start day followed by at least three consecutive calendar days where daily cases exceed the previous day. Requiring a one-day date difference prevents a gap from masquerading as an uninterrupted run.

The verified snapshot produced 1,769 matches across 170 locations. Examples include Russian Federation with 23 consecutive increases from 14 September to 7 October 2020, Spain with 20 from 24 February to 15 March 2020, and the United States with 16 from 8 to 24 March 2020. These are reproducible reporting patterns. They do not prove transmission mechanisms, policy effects, or clinical severity.

## 11. Performance optimization

### 11.1 Snowflake and API path

The warehouse is intentionally X-Small with 60-second auto-suspend because the project is interactive and low volume. A monthly resource monitor bounds total cost. The latest-country transient table reduces overview and identity work to roughly one row per location. Time-series queries project named columns, filter by resolved location and date, and never use `SELECT *`. Forecast history is capped in Snowflake. Combined page endpoints and 24-hour/6-hour cache policies reduce repeated scans.

No clustering key, materialized view, or Search Optimization Service is configured. At 61,900 mart rows, their maintenance and credit cost would likely exceed pruning benefits. This is an optimization decision, not an omission. A future scale trigger should use Query Profile evidence: bytes scanned, partitions pruned, latency percentiles, and credits per representative endpoint.

`COUNTRY_LATEST_METRICS` refreshes inside a transaction so readers never observe an intentionally empty snapshot. The tradeoff is manual refresh orchestration. The population loader's staging/swap workflow similarly favors last-known-good availability over minimal SQL.

### 11.2 Spark optimization evidence

The committed evidence was generated on 26 July 2026 with Python 3.12.13, PySpark 3.5.6, Java 17.0.19, `local[2]`, adaptive execution enabled, and 32 shuffle partitions. The immutable source contained 61,900 ECDC rows, 14 mapping rows, and 217 population rows. Every comparison passed ordered-schema, row-count, and order-independent SHA-256 multiset checks before timing.

| Comparison | Baseline median | Candidate median | Result | Engineering conclusion |
| --- | ---: | ---: | --- | --- |
| Early projection/filter | 309.199 ms | 342.216 ms | 10.7% slower | Keep projection for schema and network discipline, not as a local-speed claim |
| Broadcast mapping/population | 708.043 ms | 766.993 ms | 8.3% slower | Physical plan improved to two build-right broadcast hash joins, but tiny local input did not amortize setup |
| Adaptive duplicate aggregation | 445.400 ms | 586.483 ms | 31.7% slower | AQE remains a scale-safety setting; do not claim a speedup for this fixture |
| Reused-frame cache | 651.562 ms | 512.305 ms | 21.4% faster | Persist only the frame reused by profiling and transformation, then unpersist in `finally` |
| File layout | 3,659.314 ms | 1,932.798 ms | 47.2% faster | One output file avoids small-file overhead at this measured size |

The final Parquet output measured 733,769 bytes. Thirteen monthly partitions had a median of only 74,313 bytes, far below the 128 MiB target. The pipeline therefore writes an unpartitioned dataset and coalesces to one file. Partitioning by country or date would create tiny files and scheduler overhead. The decision is recalculated from measured bytes rather than hard-coded as a universal rule.

Explicit schemas and header inspection fail before Bronze publication on drift. Corrupt rows and quality failures are retained as run-local evidence. Immutable source, ingestion, and benchmark IDs prevent silent overwrite. Bronze manifest version 2 records source checksums, the ruleset, quality status, and quality-document checksum. Benchmark evidence is published atomically only if correctness gates pass.

The key lesson is that Spark optimization is workload-specific. Broadcast and AQE are architecturally sensible at scale but slower here. The report therefore separates physical-plan improvement from elapsed-time improvement and keeps Snowflake SQL/pandas as the production path for the current volume.

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

Dependencies are declared in `pyproject.toml` and resolved in `uv.lock`. Python 3.12 patch compatibility is declared in package metadata, while `.python-version` and Docker pin 3.12.13. Java and PySpark live only in the optional Spark image/group. GitHub Actions runs lock verification, isort, Black, Ruff, 77 application tests, and the Spark fixture suite.

Verification on 29 July 2026:

| Check | Result |
| --- | --- |
| Application/unit/contract tests | 77 passed |
| Ruff | Passed |
| Black | Passed with cache disabled because the review sandbox blocked the user cache path |
| Compose configuration | Parsed successfully |
| Live Snowflake forecast read | Passed with `COVID_APP_ROLE` |
| Docker runtime | Not run; Docker Desktop engine was stopped |
| Fresh local Spark suite | Not completed; review host had no Java runtime |
| Committed Spark evidence | Version 2; all five correctness gates passed in the recorded run |

The normal supervisor path requires only Docker plus a Snowflake account. `setup.ps1` or `setup.sh` invokes the containerized bootstrap, validates prerequisites and postconditions, loads the committed population snapshot, deploys marts, starts dependencies, creates MongoDB indexes, and performs smoke checks. Manual recovery instructions are also documented.

## 14. Limitations and prioritized next work

Before submission:

1. Confirm the spelling of the student name, commit all working-tree changes, push `api-works`, and replace the base commit reference with the final hash.
2. Run the documented setup on a clean virtual machine with Docker Desktop/Engine and capture healthy API, annotation round-trip, forecast, and dashboard evidence.
3. Re-run the Spark fixture suite through the pinned Docker service and retain the CI link or terminal summary.

Engineering follow-ups:

1. Add a production Compose override without bind mounts or reload, then add TLS, secret injection, authentication, and rate limiting.
2. Add a MongoDB JSON Schema validator and integration tests with disposable Redis/MongoDB containers.
3. Add Snowflake query tags, statement timeouts, Query Profile captures, and refresh orchestration with cache invalidation.
4. Add chart-level source notes, correction markers, downloads, and synchronized-date options.
5. Consider clustering only after defining stable features and validation; do not add it solely to satisfy a bonus label.

## 15. Conclusion

The project is a defensible end-to-end data engineering submission. It combines a managed warehouse, external data integration, schema and quality controls, a cost-aware API, operational NoSQL data, interactive visualization, required forecasting, pattern recognition, and measured Spark engineering. Its most senior characteristic is not the number of technologies; it is that tradeoffs and negative benchmark results are documented instead of being hidden.

The remaining submission risk is operational evidence, not missing core functionality: the final working tree must be pushed, and a clean-VM run should prove the complete stack under the same commit that appears in the report.

## Appendix A. Key commands

```bash
# Guided first-time setup
./setup.sh

# Application quality checks
uv run isort --check-only --diff .
uv run black --check --diff .
uv run ruff check .
uv run python -m unittest discover -s tests -v

# Spark quality and benchmark path
docker compose --profile spark run --rm spark ingest-profile \
  --source-batch-id ecdc-2020-v1 \
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
| Atomic population load | `scripts/load_population.py`, `tests/test_population_loader.py` |
| API, caching, and errors | `app/api`, `app/services`, `app/repositories`, `tests/test_api.py`, `tests/test_cache.py` |
| Forecasting | `app/services/forecasting.py`, `app/services/covid_service.py`, `tests/test_forecasting.py` |
| MongoDB annotations | `app/repositories/annotation_repository.py`, `app/services/annotation_service.py`, `tests/test_annotations.py` |
| Dashboard | `app/dashboard`, `tests/test_dashboard.py` |
| Spark Bronze, profiling, and optimization | `app/spark_pipeline`, `spark_tests`, `reports/spark/evidence.json` |
| Reproducible deployment | `README.md`, `.env.example`, `compose.yaml`, Dockerfiles, `setup.ps1`, `setup.sh` |
