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

The project uses five WDI indicators for 2019 through 2021:

| Indicator | Meaning | Use |
| --- | --- | --- |
| `SP.POP.TOTL` | Annual population | Country context only |
| `EN.POP.DNST` | Population density | Contact-environment context |
| `SP.POP.65UP.TO.ZS` | Population aged 65 and older | Age-vulnerability context |
| `NY.GDP.PCAP.KD` | Real GDP per person | Baseline and descriptive change |
| `SH.XPD.CHEX.PP.CD` | Health expenditure per person, PPP | Health-system context |

The 2019 values form the pre-pandemic baseline. WDI values do not enter the forecasting code.

See [World Bank country context](architecture/world-bank-context.md) for the snapshot, identity, coverage, and rollback rules.

### World Bank snapshot maintenance

Normal setup publishes the committed data. It does not contact the World Bank API.

Use the network refresh only when you want to review a new source snapshot:

```bash
uv run python -m scripts.world_bank_indicators refresh
```

The refresh retrieves every result page for the indicator allowlist. It validates identities and coverage before it writes candidate files.

Review the WDI CSV, manifest, identity report, and coverage report together. A failed check cannot replace the committed files.

The raw WDI grain is:

```text
SNAPSHOT_ID + CANONICAL_ISO3 + INDICATOR_CODE + OBSERVATION_YEAR
```

Country names are display values. The project does not use them as WDI join keys.

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
    -> MARTS.COVID_ENRICHED_EXTENDED
    -> MARTS.COUNTRY_LATEST_METRICS_EXTENDED
```
