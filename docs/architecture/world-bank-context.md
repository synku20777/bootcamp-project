# Versioned World Bank country context

This document is the authoritative architecture for World Development Indicators (WDI) data. Older population-only setup notes describe the compatibility path that seeds the frozen epidemiological denominator; they do not define the active context model.

## Analytical boundaries

| Concept | Source and period | Why it is separate |
| --- | --- | --- |
| Active WDI context | WDI source 2, 2019–2021 | Preserves the currently published source observations and revisions |
| Pre-pandemic baseline | 2019 values | Establishes temporal ordering before COVID outcomes and reduces leakage risk |
| Pandemic-period change | Real GDP per capita changes from 2019 to 2020/2021 | Describes observed change; it does not identify a causal COVID effect |
| COVID rate denominator | Frozen legacy 2020 population | Prevents a routine WDI revision from silently rewriting published per-capita rates |
| Forecast input | COVID report date and incident metric only | Prevents socioeconomic fields or snapshot state from entering forecasting |

The project uses exactly five indicators:

| Code | Warehouse meaning | Rationale |
| --- | --- | --- |
| `SP.POP.TOTL` | Population context | Provides source-faithful annual population while the rate denominator remains frozen separately |
| `EN.POP.DNST` | Population density | Captures contact-environment context that is not available in the epidemiological fact table |
| `SP.POP.65UP.TO.ZS` | Population aged 65+ | Represents pre-pandemic age vulnerability without importing a duplicate population count |
| `NY.GDP.PCAP.KD` | Real GDP per capita, constant 2015 US$ | Supports comparable per-person economic context and descriptive 2019–2021 changes; it is never labelled total GDP or “GDP impact” |
| `SH.XPD.CHEX.PP.CD` | Current health expenditure per capita, PPP | Offers purchasing-power-adjusted cross-country context; no change metric is published because current international dollars are not a real inflation-adjusted time series |

No total-GDP indicator is loaded in this iteration.

## Snapshot lifecycle

The explicit refresh command is the only networked workflow:

```bash
uv run python -m scripts.world_bank_indicators refresh
```

It pins API v2 to WDI source 2, requests the indicator allowlist for 2019–2021, retrieves every page, validates identities and coverage, and writes a new immutable CSV and manifest only when the logical observation checksum changes. Normal setup is network-independent:

```bash
uv run python -m scripts.world_bank_indicators publish --validate-only
./setup.sh
```

The publisher first validates the committed file, loads a candidate, and checks duplicate keys. Observation insertion and inactive registry insertion share one transaction. A second controlled transaction supersedes the previous snapshot and activates the candidate. `STAGING.WORLD_BANK_COUNTRY_INDICATORS_CURRENT` is the only supported raw-data entry point for downstream transformations.

`RAW.WORLD_BANK_COUNTRY_INDICATORS` has immutable grain `SNAPSHOT_ID + CANONICAL_ISO3 + INDICATOR_CODE + OBSERVATION_YEAR`. `RAW.WORLD_BANK_INDICATOR_SNAPSHOTS` has one row per snapshot and uses `CANDIDATE`, `ACTIVE`, `SUPERSEDED`, `ROLLED_BACK`, and `REJECTED` publication states. Exactly one row may be both `ACTIVE` and `IS_ACTIVE`; the publisher enforces this invariant because standard Snowflake table keys are informational.

Snowflake standard-table primary and unique constraints document the grain but do not enforce it. Publication therefore fails on every duplicate, including byte-for-byte exact duplicates, before activation. Exactly one registry record must be active before and after a change. Downstream failure reactivates the recorded predecessor and rebuilds the previous marts.

## Deterministic checksums

`app/world_bank.py` owns the cross-platform serialization protocol. It fixes column and row order, normalizes Unicode to NFC, emits UTF-8 with LF endings and one final newline, uses RFC 4180 CSV quoting, represents null as `\N`, and formats decimals without scientific notation or insignificant trailing zeros. Logical equality therefore treats `123.4` and `123.400000000` as the same value.

Two hashes serve different purposes:

- Observation checksum: logical source identity; excludes retrieval time, snapshot ID, raw text and local paths.
- File checksum: exact bytes of the committed CSV, including operational provenance fields.

Golden tests fix the logical checksum for a representative decimal/null record, and the Spark fixture parses the same record through `DecimalType(38, 9)` before applying the shared protocol. This catches operating-system newline drift and Python/Spark scalar-format drift without maintaining two checksum implementations.

## Identity policy

Country names are display attributes, never join keys. A World Bank entity is accepted only when it is a non-aggregate economy with a canonical ISO3 or a reviewed explicit mapping in `data/reference/world_bank_entity_mapping.csv`. Aggregate and unsupported entities remain in manifest evidence but are not inserted into the canonical observation table. Any conflicting mapping blocks publication.

`MARTS.DIM_COUNTRY` is driven from normalized ECDC identities. COVID locations without a supported ISO3 remain available to epidemiological APIs but receive no WDI context. Baseline marts start from this dimension and left-join observations so an eligible country with five missing indicators remains visible as a row with explicit `missing` statuses.

Refresh evidence reports received World Bank entities, excluded aggregates, direct ISO matches, reviewed explicit mappings, unsupported economies, missing identities, and conflicts. Conflicts fail publication. Unsupported and aggregate entities remain in the manifest evidence but are never inserted into the canonical observation table.

## Coverage gates

Coverage is evaluated independently for each indicator/year pair against the canonical non-aggregate World Bank economy set:

| Indicator | Minimum non-null coverage | Maximum permitted drop |
| --- | ---: | ---: |
| `SP.POP.TOTL` | 95% | 5 percentage points |
| `EN.POP.DNST` | 90% | 5 percentage points |
| `SP.POP.65UP.TO.ZS` | 90% | 5 percentage points |
| `NY.GDP.PCAP.KD` | 80% | 5 percentage points |
| `SH.XPD.CHEX.PP.CD` | 75% | 10 percentage points |

An absent indicator/year, duplicate key, conflicting identity, excessive coverage drop, or canonical-country loss beyond `max(5, 2% of the previous count)` rejects the candidate. Drops greater than one percentage point that remain within the hard limit are warnings. Indicator-specific gates are intentional because health-expenditure publication is sparser than population publication.

## Warehouse build order

1. Normalize COVID data and create `MARTS.DIM_COUNTRY`.
2. Publish the snapshot registry and active-snapshot view.
3. Validate the clean WDI view.
4. Seed `MARTS.COUNTRY_COVID_DENOMINATOR` from the legacy committed population file once.
5. Build `MARTS.COUNTRY_BASELINE_2019`.
6. Build `MARTS.COUNTRY_INDICATOR_ANNUAL`.
7. Build `MARTS.COVID_ENRICHED` from the frozen denominator only.
8. Build the latest COVID snapshot.
9. Expose `MARTS.COUNTRY_CONTEXT_ANALYSIS`.

This order has no circular dependency. The baseline's 2020 population is named `POPULATION_2020_CONTEXT`; the epidemiological value is named `COVID_RATE_POPULATION_2020` throughout the mart and public API.

## API and cache behavior

`GET /countries/{identifier}/context` returns values with year, unit, indicator code, status and snapshot ID. The combined Country Explorer payload carries the same context without another browser request.

The application reads the committed snapshot ID at startup. A matching active Snowflake snapshot enables context. A mismatch returns `503 context_data_unavailable` from the context endpoint, while summary, time-series and forecasting remain available. Country Explorer shows COVID data plus a context warning. Context cache keys are visibly versioned as:

```text
covid-api:v3:<snapshot-id>:country-context:<iso3>
```

The service must restart after a new manifest is deployed so process state and the active warehouse snapshot converge deliberately.

## Spark optimization boundary

Spark retains the full historical WDI file as Bronze audit data but derives a narrow one-row-per-ISO3 baseline before joining. Only that narrow projection is broadcast. Broadcasting the long-form history would increase driver/executor transfer, risk one-to-many row expansion, and repeat pivot work. At this size the WDI data is written as a small number of Parquet files without indicator/year directory partitioning because those directories would mostly add metadata overhead.

ECDC normally supplies ISO2, so Spark builds typed `ISO2:`/`ISO3:` lookup keys for the narrow baseline; it never joins context by display name. Source export fingerprints the live Snowflake baseline with a shared decimal/null protocol. Spark computes the same fingerprint after parsing and joining, and publication fails on any row-count or checksum mismatch. Evidence version 3 records snapshot ID, baseline rows, canonical projection bytes, joined COVID rows, unmatched locations, and both fingerprints.

## Migration and rollback

Run `scripts/reconcile_world_bank_migration.py` before consumer cutover. It compares legacy and new outputs at ISO3/report-date grain, requires exact cases, deaths and denominator values, and applies tolerances of 0.01 per 100,000 and 0.0001 mortality percentage points.

After one verified release, `sql/08_migrate_population_compatibility.sql` renames the original table and exposes its old contract as a compatibility view over the frozen denominator. Retain the legacy object for a complete release cycle. Snapshot rollback and denominator rollback are separate operations: source revision must never implicitly change the epidemiological denominator.

The legacy source loader is skipped once a non-empty, fully frozen denominator exists. A future denominator revision must therefore be an explicit reviewed migration with a new `DENOMINATOR_VERSION`, row-level rate reconciliation, approval timestamp, and rollback artifact; publishing a WDI snapshot alone has no permission to change it.

The controlled workflow is:

```bash
uv run python -m scripts.update_covid_denominator plan \
  --version v2 --output reports/world_bank/denominator-v2-plan.json

uv run python -m scripts.update_covid_denominator apply \
  --evidence reports/world_bank/denominator-v2-plan.json --approve
```

Planning compares every current country to active WDI population and records maximum per-capita rate movement. A no-change candidate is rejected so version numbers cannot advance without an actual policy change. Apply recomputes the semantic fingerprint so stale evidence cannot authorize changed source state, archives the current version in `COUNTRY_COVID_DENOMINATOR_HISTORY`, and then updates atomically. `rollback --version <version> --approve` restores an archived version. Marts and the row-level migration reconciliation must be rebuilt after apply or rollback before application restart.
