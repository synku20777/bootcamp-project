# Versioned World Bank country context

This document defines the World Development Indicators (WDI) architecture. Older population-only notes describe the compatibility path for the frozen epidemiological denominator.

Those notes do not define the active context model.

## Analytical boundaries

| Concept | Source and period | Why it is separate |
| --- | --- | --- |
| Active WDI context | WDI source 2, 2019–2021 | Preserves the currently published source observations and revisions |
| Pre-pandemic baseline | 2019 values | Establishes temporal ordering before COVID outcomes and reduces leakage risk |
| Pandemic-period change | Real GDP per capita changes from 2019 to 2020/2021 | Describes observed change. It does not identify a causal COVID effect. |
| COVID rate denominator | Frozen legacy 2020 population | Prevents a routine WDI revision from silently rewriting published per-capita rates |
| Forecast input | COVID report date and incident metric only | Prevents socioeconomic fields or snapshot state from entering forecasting |

The project uses exactly five indicators:

| Code | Warehouse meaning | Rationale |
| --- | --- | --- |
| `SP.POP.TOTL` | Population context | Provides source-faithful annual population while the rate denominator remains frozen separately |
| `EN.POP.DNST` | Population density | Captures contact-environment context that is not available in the epidemiological fact table |
| `SP.POP.65UP.TO.ZS` | Population aged 65+ | Represents pre-pandemic age vulnerability without importing a duplicate population count |
| `NY.GDP.PCAP.KD` | Real GDP per capita, constant 2015 US$ | Supports comparable per-person economic context and descriptive 2019–2021 changes. The project never labels it total GDP or “GDP impact.” |
| `SH.XPD.CHEX.PP.CD` | Current health expenditure per capita, PPP | Gives purchasing-power-adjusted cross-country context. The project does not publish a change metric because this is not a real inflation-adjusted series. |

This iteration does not load a total-GDP indicator.

## Snapshot lifecycle

The explicit refresh command is the only networked workflow:

```bash
uv run python -m scripts.world_bank_indicators refresh
```

The command uses API v2 and WDI source 2. It requests the indicator allowlist for 2019–2021 and retrieves every page.

The command validates identity and coverage. It writes a new immutable CSV and manifest only when the logical observation checksum changes.

Normal setup does not use the network:

```bash
uv run python -m scripts.world_bank_indicators publish --validate-only
./setup.sh
```

The publisher first validates the committed file, loads a candidate, and checks duplicate keys. Observation insertion and inactive registry insertion share one transaction. A second controlled transaction supersedes the previous snapshot and activates the candidate. `STAGING.WORLD_BANK_COUNTRY_INDICATORS_CURRENT` is the only supported raw-data entry point for downstream transformations.

`RAW.WORLD_BANK_COUNTRY_INDICATORS` has immutable grain `SNAPSHOT_ID + CANONICAL_ISO3 + INDICATOR_CODE + OBSERVATION_YEAR`.

`RAW.WORLD_BANK_INDICATOR_SNAPSHOTS` has one row per snapshot. It uses `CANDIDATE`, `ACTIVE`, `SUPERSEDED`, `ROLLED_BACK`, and `REJECTED` publication states.

Exactly one row can be both `ACTIVE` and `IS_ACTIVE`. The publisher enforces this rule because standard Snowflake table keys are informational.

Snowflake standard-table primary and unique constraints document the grain but do not enforce it. Publication therefore fails on every duplicate, including byte-for-byte exact duplicates, before activation. Exactly one registry record must be active before and after a change. Downstream failure reactivates the recorded predecessor and rebuilds the previous marts.

## Deterministic checksums

`app/world_bank.py` owns the cross-platform serialization protocol. The protocol fixes column and row order and normalizes Unicode to NFC.

It emits UTF-8 with LF endings and one final newline. It uses RFC 4180 CSV quoting and represents null as `\N`.

The protocol formats decimals without scientific notation or insignificant trailing zeros. Logical equality treats `123.4` and `123.400000000` as the same value.

Two hashes serve different purposes:

- Observation checksum: identifies the logical source. It excludes retrieval time, snapshot ID, raw text, and local paths.
- File checksum: exact bytes of the committed CSV, including operational provenance fields.

Golden tests fix the logical checksum for a representative decimal and null record. The Spark fixture parses the same record through `DecimalType(38, 9)`.

The fixture then applies the shared protocol. This process finds operating-system newline drift and Python or Spark scalar-format drift.

## Identity policy

Country names are display attributes, not join keys. The publisher accepts only non-aggregate economies with a canonical ISO3 or a reviewed explicit mapping.

The mapping file is `data/reference/world_bank_entity_mapping.csv`. Aggregate and unsupported entities remain in manifest evidence but do not enter the canonical observation table.

Any conflicting mapping blocks publication.

Normalized ECDC identities define `MARTS.DIM_COUNTRY`. COVID locations without a supported ISO3 remain available to epidemiological APIs but receive no WDI context.

Baseline marts start from this dimension and left-join observations. An eligible country remains visible when all five indicators are missing.

The row contains an explicit `missing` status for each absent indicator.

Refresh evidence reports received World Bank entities, excluded aggregates, direct ISO matches, reviewed explicit mappings, unsupported economies, missing identities, and conflicts. Conflicts fail publication. Unsupported and aggregate entities remain in the manifest evidence but are never inserted into the canonical observation table.

## Coverage gates

The publisher evaluates each indicator and year against the canonical non-aggregate World Bank economy set:

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

This order has no circular dependency. The baseline 2020 population is `POPULATION_2020_CONTEXT`.

The epidemiological value is `COVID_RATE_POPULATION_2020` in the mart and public API.

## API and cache behavior

`GET /countries/{identifier}/context` returns values with year, unit, indicator code, status and snapshot ID. The combined Country Explorer payload carries the same context without another browser request.

The application reads the committed snapshot ID at startup. A matching active Snowflake snapshot enables context. A mismatch returns `503 context_data_unavailable` from the context endpoint, while summary, time-series and forecasting remain available. Country Explorer shows COVID data plus a context warning. WDI-only context cache keys are visibly versioned as:

```text
covid-api:v4:<snapshot-id>:country-context:<iso3>
```

Restart the service after you deploy a new manifest. This action aligns process state with the active warehouse snapshot.

Every COVID-derived key includes `COVID_DATASET`. If the service cannot read the optional manifest, combined pages use the explicit revision `unavailable`.

The context-only route still fails closed.

## Spark optimization boundary

Spark retains the full historical WDI file as Bronze audit data. It derives a narrow one-row-per-ISO3 baseline before the join.

Spark broadcasts only this narrow projection. Broadcasting the long-form history would increase data transfer, risk row expansion, and repeat pivot work.

The job writes a small number of Parquet files at this size. Indicator and year directories would add unnecessary metadata.

ECDC normally supplies ISO2. Spark builds typed `ISO2:` and `ISO3:` lookup keys for the narrow baseline.

Spark does not join context by display name. Source export fingerprints the live Snowflake baseline with a shared decimal and null protocol.

Spark calculates the same fingerprint after parsing and joining. A row-count or checksum mismatch blocks publication.

Evidence version 3 records the snapshot ID, baseline rows, projection bytes, joined COVID rows, unmatched locations, and both fingerprints.

## Migration and rollback

Run `scripts/reconcile_world_bank_migration.py` before consumer cutover. The script compares legacy and new outputs at ISO3 and report-date grain.

It requires exact case, death, and denominator values. It allows 0.01 per 100,000 and 0.0001 mortality percentage points.

After one verified release, `sql/08_migrate_population_compatibility.sql` renames the original table and exposes its old contract as a compatibility view over the frozen denominator. Retain the legacy object for a complete release cycle. Snapshot rollback and denominator rollback are separate operations: source revision must never implicitly change the epidemiological denominator.

The project skips the legacy source loader when a non-empty frozen denominator exists. A future denominator revision requires an explicit reviewed migration.

The migration needs a new `DENOMINATOR_VERSION`, row-level rate reconciliation, an approval timestamp, and a rollback artifact. WDI snapshot publication cannot change the denominator.

The controlled workflow is:

```bash
uv run python -m scripts.update_covid_denominator plan \
  --version v2 --output reports/world_bank/denominator-v2-plan.json

uv run python -m scripts.update_covid_denominator apply \
  --evidence reports/world_bank/denominator-v2-plan.json --approve
```

The plan compares each current country to the active WDI population. It records the maximum per-capita rate movement.

The command rejects a no-change candidate. A version number cannot advance without an actual policy change.

The apply command recalculates the semantic fingerprint. Stale evidence cannot authorize a changed source state.

The command archives the current version in `COUNTRY_COVID_DENOMINATOR_HISTORY` and then updates the denominator atomically. `rollback --version <version> --approve` restores an archived version.

After apply or rollback, rebuild the marts and the row-level reconciliation. Then restart the application.
