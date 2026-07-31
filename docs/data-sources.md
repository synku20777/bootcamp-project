# Data sources

## COVID facts

The production API uses two Snowflake Marketplace tables:

- `COVID19_EPIDEMIOLOGICAL_DATA.PUBLIC.ECDC_GLOBAL`
- `COVID19_EPIDEMIOLOGICAL_DATA.PUBLIC.JHU_COVID_19_TIMESERIES`

`ECDC_GLOBAL` supplies daily country-level case and death measures. The project preserves negative source corrections.

`JHU_COVID_19_TIMESERIES` contains cumulative measures at several geographic levels. The extension normalizes it to one ISO3 row per date.

The project splices the two sources. It does not blend overlapping observations.

Shared countries use ECDC through the governed boundary. They use JHU after that boundary.

Spain switches on 2020-12-14. Other shared countries switch on 2020-12-15.

Eight JHU-only countries keep their JHU history from 2020-01-22.

Set `COVID_DATASET=extended` to use the promoted series. Set `COVID_DATASET=legacy` to use the ECDC-only marts.

Spark uses ECDC for transformation benchmarks. It uses the governed extended mart for offline clustering.

Spark does not calculate another splice boundary. This rule keeps one source policy in Snowflake.

## Sources not used as COVID facts

| Group | Examples | Reason |
| --- | --- | --- |
| Weekly data | `ECDC_GLOBAL_WEEKLY` | Weekly rows do not support daily forecasts or patterns |
| Other global feeds | `JHU_COVID_19`, `WHO_TIMESERIES` | They do not use the reviewed extension contract |
| Country feeds | `NYT_US_COVID19`, `PCM_DPS_COVID19`, `SCS_BE_*` | Their geographic grain does not match the global model |
| Mobility and policy | `APPLE_MOBILITY`, `GOOG_GLOBAL_MOBILITY_REPORT`, `HDX_ACAPS` | They require a separate lag and causal study |
| Vaccination | `JHU_VACCINES`, `OWID_VACCINATIONS` | Their main period is later than the fact window |
| Model output | `IHME_COVID_19` | Model output cannot replace observed facts |

## World Bank context

The project uses WDI source 2 for 2019 through 2021. It accepts these indicators:

- `SP.POP.TOTL`
- `EN.POP.DNST`
- `SP.POP.65UP.TO.ZS`
- `NY.GDP.PCAP.KD`
- `SH.XPD.CHEX.PP.CD`

The 2019 values form the pre-pandemic baseline. This order reduces temporal leakage in descriptive comparisons.

WDI values do not enter the forecasting code. The forecast uses only COVID dates and incident measures.

See [World Bank country context](architecture/world-bank-context.md) for indicator rationale, snapshots, identity, coverage, and rollback.

## Population denominator

The project keeps two population values:

- `POPULATION_2020_CONTEXT` is the active WDI context value.
- `COVID_RATE_POPULATION_2020` is the frozen COVID rate denominator.

A normal WDI publication cannot change the frozen denominator. A denominator change requires a reviewed migration and reconciliation.

## Data flow

The deployment creates the data objects in this order:

```text
COVID19_EPIDEMIOLOGICAL_DATA.PUBLIC.ECDC_GLOBAL
    + COVID_ANALYTICS.RAW.COUNTRY_CODE_MAPPING
    -> COVID_ANALYTICS.STAGING.COVID_COUNTRY_DAILY

World Bank API
    -> reviewed CSV, manifest, and evidence
    -> RAW.WORLD_BANK_COUNTRY_INDICATORS
    -> STAGING.WORLD_BANK_COUNTRY_INDICATORS_CURRENT
    -> MARTS.COUNTRY_BASELINE_2019
    -> MARTS.COUNTRY_INDICATOR_ANNUAL

COVID_ANALYTICS.STAGING.COVID_COUNTRY_DAILY
    + MARTS.COUNTRY_COVID_DENOMINATOR
    -> MARTS.COVID_ENRICHED
    -> MARTS.COUNTRY_LATEST_METRICS

MARTS.COUNTRY_BASELINE_2019
    + MARTS.COUNTRY_INDICATOR_ANNUAL
    + MARTS.COUNTRY_LATEST_METRICS
    -> MARTS.COUNTRY_CONTEXT_ANALYSIS

COVID_ANALYTICS.STAGING.COVID_COUNTRY_DAILY
    -> MARTS.CASE_INCREASE_PATTERNS

COVID19_EPIDEMIOLOGICAL_DATA.PUBLIC.JHU_COVID_19_TIMESERIES
    + RAW.JHU_GEOGRAPHY_POLICY
    + STAGING.CANONICAL_COUNTRY_CODE_MAP
    -> STAGING.JHU_COUNTRY_CUMULATIVE
    -> STAGING.JHU_COUNTRY_DAILY

STAGING.COVID_COUNTRY_DAILY
    + STAGING.JHU_COUNTRY_DAILY
    -> STAGING.COVID_COUNTRY_DAILY_EXTENDED

STAGING.COVID_COUNTRY_DAILY_EXTENDED
    -> MARTS.CASE_INCREASE_PATTERNS_EXTENDED_DATA
    -> MARTS.CASE_INCREASE_PATTERNS_EXTENDED

STAGING.COVID_COUNTRY_DAILY_EXTENDED
    + MARTS.COUNTRY_COVID_DENOMINATOR_EXTENDED
    -> MARTS.COVID_ENRICHED_EXTENDED_DATA
    -> MARTS.COVID_ENRICHED_EXTENDED
    -> MARTS.COUNTRY_LATEST_METRICS_EXTENDED
```
