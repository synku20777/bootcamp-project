from __future__ import annotations

import argparse
import hashlib
import json
import math
import secrets
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import snowflake.connector
from dotenv import load_dotenv
from snowflake.connector.pandas_tools import write_pandas

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.world_bank import (  # noqa: E402
    API_VERSION,
    INDICATOR_BY_CODE,
    INDICATOR_CODES,
    OBSERVATION_YEARS,
    SOURCE_ID,
    SOURCE_NAME,
    WorldBankValidationError,
    duplicate_report,
    load_snapshot,
    normalize_decimal,
    observation_checksum,
    snapshot_csv_bytes,
    validate_candidate,
)
from scripts.load_population import connect_to_snowflake  # noqa: E402

DEFAULT_CSV_PATH = Path("data/external/world_bank_indicators_2019_2021.csv")
DEFAULT_MANIFEST_PATH = Path(
    "data/external/world_bank_indicators_2019_2021.manifest.json"
)
DEFAULT_MAPPING_PATH = Path("data/reference/world_bank_entity_mapping.csv")
API_ROOT = f"https://api.worldbank.org/{API_VERSION}"
TARGET_DATABASE = "COVID_ANALYTICS"
RAW_SCHEMA = "RAW"
OBSERVATION_TABLE = "WORLD_BANK_COUNTRY_INDICATORS"
SNAPSHOT_TABLE = "WORLD_BANK_INDICATOR_SNAPSHOTS"


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{path.name}.{secrets.token_hex(6)}.tmp"
    try:
        staging.write_bytes(content)
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def _request_page(
    endpoint: str, params: dict[str, Any], page: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    response = requests.get(
        f"{API_ROOT}/{endpoint}",
        params={**params, "page": page, "format": "json", "source": SOURCE_ID},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    if (
        not isinstance(payload, list)
        or len(payload) < 2
        or not isinstance(payload[0], dict)
    ):
        raise WorldBankValidationError(
            "World Bank returned an unexpected response shape."
        )
    return payload[0], payload[1] or []


def request_all_pages(
    endpoint: str, params: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    metadata, records = _request_page(endpoint, params, 1)
    pages = int(metadata.get("pages", 0))
    if pages < 1:
        raise WorldBankValidationError("World Bank pagination metadata is invalid.")
    all_records = list(records)
    for page in range(2, pages + 1):
        page_metadata, page_records = _request_page(endpoint, params, page)
        if int(page_metadata.get("pages", 0)) != pages:
            raise WorldBankValidationError(
                "World Bank pagination changed during retrieval."
            )
        all_records.extend(page_records)
    if len(all_records) != int(metadata.get("total", len(all_records))):
        raise WorldBankValidationError("Not all World Bank pages were retrieved.")
    return all_records, metadata


def _explicit_mappings(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    frame = pd.read_csv(path, dtype="string", keep_default_na=False)
    required = {"SOURCE_ENTITY_CODE", "CANONICAL_ISO2", "CANONICAL_ISO3", "NOTES"}
    if set(frame.columns) != required:
        raise WorldBankValidationError(
            "The World Bank mapping registry has invalid columns."
        )
    if frame["SOURCE_ENTITY_CODE"].duplicated().any():
        raise WorldBankValidationError(
            "The World Bank mapping registry has duplicate keys."
        )
    return {
        str(row.SOURCE_ENTITY_CODE): {
            "iso2": str(row.CANONICAL_ISO2),
            "iso3": str(row.CANONICAL_ISO3),
        }
        for row in frame.itertuples(index=False)
    }


def _identity_catalog(
    mapping_path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    records, _ = request_all_pages("country", {"per_page": 400})
    explicit = _explicit_mappings(mapping_path)
    catalog: dict[str, dict[str, Any]] = {}
    counts = {
        "received_entities": len(records),
        "aggregate_excluded": 0,
        "matched_iso": 0,
        "matched_explicit_mapping": 0,
        "unsupported_world_bank_economy": 0,
        "missing_identity": 0,
        "conflicting_identity": 0,
    }
    excluded: list[dict[str, Any]] = []
    for record in records:
        source_code = str(record.get("id") or "").strip().upper()
        iso2 = str(record.get("iso2Code") or "").strip().upper()
        region_id = str((record.get("region") or {}).get("id") or "")
        is_aggregate = region_id == "NA"
        status = "matched_iso"
        canonical_iso2 = iso2
        canonical_iso3 = source_code
        if source_code in explicit:
            mapped = explicit[source_code]
            if iso2 and mapped["iso2"] and iso2 != mapped["iso2"]:
                status = "conflicting_identity"
            else:
                status = "matched_explicit_mapping"
                canonical_iso2 = mapped["iso2"]
                canonical_iso3 = mapped["iso3"]
        elif is_aggregate:
            status = "aggregate_excluded"
        elif len(source_code) != 3 or len(iso2) != 2:
            status = "missing_identity"
        counts[status] += 1
        identity = {
            "SOURCE_ENTITY_CODE": source_code,
            "CANONICAL_ISO2": canonical_iso2 or None,
            "CANONICAL_ISO3": canonical_iso3 or None,
            "COUNTRY_NAME": record.get("name"),
            "CODE_SYSTEM": (
                "ISO-3166-1" if status == "matched_iso" else "PROJECT_MAPPING"
            ),
            "ENTITY_TYPE": "aggregate" if is_aggregate else "economy",
            "IS_AGGREGATE": is_aggregate,
            "IDENTITY_MAPPING_STATUS": status,
        }
        catalog[iso2 or source_code] = identity
        if status not in {"matched_iso", "matched_explicit_mapping"}:
            excluded.append(identity)
    if counts["conflicting_identity"]:
        raise WorldBankValidationError(
            "The identity registry conflicts with World Bank metadata."
        )
    return catalog, {"counts": counts, "excluded_entities": excluded}


def build_candidate(
    mapping_path: Path = DEFAULT_MAPPING_PATH,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    identities, identity_report = _identity_catalog(mapping_path)
    retrieved_at = _utc_now()
    records, metadata = request_all_pages(
        f"country/all/indicator/{';'.join(INDICATOR_CODES)}",
        {"date": f"{OBSERVATION_YEARS[0]}:{OBSERVATION_YEARS[-1]}", "per_page": 20000},
    )
    source_last_updated = metadata.get("lastupdated")
    rows: list[dict[str, Any]] = []
    seen_indicators: set[str] = set()
    seen_years: set[int] = set()
    for record in records:
        indicator_code = str((record.get("indicator") or {}).get("id") or "")
        if indicator_code not in INDICATOR_BY_CODE:
            raise WorldBankValidationError(
                f"Unexpected indicator returned: {indicator_code}"
            )
        year = int(record.get("date"))
        if year not in OBSERVATION_YEARS:
            raise WorldBankValidationError(
                f"Unexpected observation year returned: {year}"
            )
        source_entity = str((record.get("country") or {}).get("id") or "").upper()
        identity = identities.get(source_entity)
        if not identity or identity["IDENTITY_MAPPING_STATUS"] not in {
            "matched_iso",
            "matched_explicit_mapping",
        }:
            continue
        definition = INDICATOR_BY_CODE[indicator_code]
        raw_value = record.get("value")
        rows.append(
            {
                **identity,
                "SNAPSHOT_ID": "candidate",
                "INDICATOR_CODE": indicator_code,
                "INDICATOR_NAME": definition.name,
                "INDICATOR_UNIT": definition.unit,
                "SOURCE_ID": SOURCE_ID,
                "SOURCE_NAME": SOURCE_NAME,
                "OBSERVATION_YEAR": year,
                "INDICATOR_VALUE": (
                    None if raw_value is None else normalize_decimal(raw_value)
                ),
                "SOURCE_LAST_UPDATED": source_last_updated,
                "OBSERVATION_STATUS": record.get("obs_status") or None,
                "SOURCE_DECIMAL_PRECISION": record.get("decimal"),
                "RAW_VALUE_TEXT": None if raw_value is None else str(raw_value),
                "RETRIEVED_AT": retrieved_at,
            }
        )
        seen_indicators.add(indicator_code)
        seen_years.add(year)
    if seen_indicators != set(INDICATOR_CODES) or seen_years != set(OBSERVATION_YEARS):
        raise WorldBankValidationError(
            "The API response omitted a requested indicator or year."
        )
    provisional_checksum = observation_checksum(rows)
    snapshot_id = f"wdi2-2019-2021-{provisional_checksum[:16]}"
    for row in rows:
        row["SNAPSHOT_ID"] = snapshot_id
    validate_candidate(rows)
    duplicates = duplicate_report(rows)
    evidence = {
        "identity": identity_report,
        "duplicates": duplicates,
        "api": {
            "version": API_VERSION,
            "source_id": SOURCE_ID,
            "years": list(OBSERVATION_YEARS),
            "indicators": list(INDICATOR_CODES),
            "pages": int(metadata.get("pages", 0)),
            "source_last_updated": source_last_updated,
        },
    }
    return rows, evidence


def coverage_report(
    rows: Iterable[dict[str, Any]], previous: dict[str, Any] | None = None
) -> dict[str, Any]:
    materialized = list(rows)
    countries = {row["CANONICAL_ISO3"] for row in materialized}
    eligible = len(countries)
    previous_pairs = (previous or {}).get("coverage", {})
    pairs: dict[str, Any] = {}
    failures: list[str] = []
    warnings: list[str] = []
    for code, definition in INDICATOR_BY_CODE.items():
        for year in OBSERVATION_YEARS:
            key = f"{code}:{year}"
            matching = [
                row
                for row in materialized
                if row["INDICATOR_CODE"] == code
                and int(row["OBSERVATION_YEAR"]) == year
            ]
            non_null = sum(row.get("INDICATOR_VALUE") is not None for row in matching)
            coverage = (
                Decimal(non_null * 100) / Decimal(eligible) if eligible else Decimal(0)
            )
            previous_coverage = previous_pairs.get(key, {}).get("coverage_pct")
            drop = None
            if previous_coverage is not None:
                drop = Decimal(str(previous_coverage)) - coverage
                if drop > definition.maximum_drop_pct_points:
                    failures.append(f"{key} coverage dropped {drop} percentage points")
                elif drop > 1:
                    warnings.append(f"{key} coverage dropped {drop} percentage points")
            if coverage < definition.minimum_coverage_pct:
                failures.append(
                    f"{key} coverage {coverage} is below {definition.minimum_coverage_pct}"
                )
            pairs[key] = {
                "eligible_country_count": eligible,
                "non_null_count": non_null,
                "null_count": eligible - non_null,
                "coverage_pct": float(coverage.quantize(Decimal("0.01"))),
                "change_from_active_pct_points": None if drop is None else float(-drop),
            }
    previous_country_count = int((previous or {}).get("country_count", 0))
    if previous_country_count:
        allowed_drop = max(5, math.ceil(previous_country_count * 0.02))
        actual_drop = previous_country_count - eligible
        if actual_drop > allowed_drop:
            failures.append(
                "Matched canonical-country count dropped by "
                f"{actual_drop}; the allowed drop is {allowed_drop}."
            )
    return {"coverage": pairs, "failures": failures, "warnings": warnings}


def refresh_snapshot(
    csv_path: Path, manifest_path: Path, mapping_path: Path
) -> dict[str, Any]:
    previous: dict[str, Any] | None = None
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WorldBankValidationError(
                "The previous committed WDI manifest is unreadable."
            ) from exc
    rows, evidence = build_candidate(mapping_path)
    coverage = coverage_report(rows, previous)
    if coverage["failures"]:
        raise WorldBankValidationError("; ".join(coverage["failures"]))
    csv_bytes = snapshot_csv_bytes(rows)
    checksum = observation_checksum(rows)
    manifest = {
        "snapshot_schema_version": 1,
        "snapshot_id": rows[0]["SNAPSHOT_ID"],
        "source_id": SOURCE_ID,
        "source_name": SOURCE_NAME,
        "years": list(OBSERVATION_YEARS),
        "indicator_codes": list(INDICATOR_CODES),
        "row_count": len(rows),
        "country_count": len({row["CANONICAL_ISO3"] for row in rows}),
        "null_value_count": sum(row["INDICATOR_VALUE"] is None for row in rows),
        "source_last_updated": rows[0]["SOURCE_LAST_UPDATED"],
        "retrieved_at": rows[0]["RETRIEVED_AT"],
        "observation_checksum": checksum,
        "file_checksum": hashlib.sha256(csv_bytes).hexdigest(),
        **evidence,
        **coverage,
    }
    if previous and previous.get("observation_checksum") == checksum:
        # Retrieval timestamps are operational metadata, not a reason to mutate
        # an immutable logical snapshot that has already been reviewed.
        return previous
    _atomic_write_bytes(csv_path, csv_bytes)
    _atomic_write_bytes(
        manifest_path,
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return manifest


def _ensure_tables(cursor: Any) -> None:
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {TARGET_DATABASE}.{RAW_SCHEMA}.{OBSERVATION_TABLE} (
            SNAPSHOT_ID VARCHAR NOT NULL,
            SOURCE_ENTITY_CODE VARCHAR NOT NULL,
            CANONICAL_ISO2 VARCHAR,
            CANONICAL_ISO3 VARCHAR NOT NULL,
            COUNTRY_NAME VARCHAR NOT NULL,
            CODE_SYSTEM VARCHAR NOT NULL,
            ENTITY_TYPE VARCHAR NOT NULL,
            IS_AGGREGATE BOOLEAN NOT NULL,
            IDENTITY_MAPPING_STATUS VARCHAR NOT NULL,
            INDICATOR_CODE VARCHAR NOT NULL,
            INDICATOR_NAME VARCHAR NOT NULL,
            INDICATOR_UNIT VARCHAR NOT NULL,
            SOURCE_ID NUMBER NOT NULL,
            SOURCE_NAME VARCHAR NOT NULL,
            OBSERVATION_YEAR NUMBER(4, 0) NOT NULL,
            INDICATOR_VALUE NUMBER(38, 9),
            SOURCE_LAST_UPDATED VARCHAR,
            OBSERVATION_STATUS VARCHAR,
            SOURCE_DECIMAL_PRECISION NUMBER,
            RAW_VALUE_TEXT VARCHAR,
            RETRIEVED_AT TIMESTAMP_TZ NOT NULL,
            CONSTRAINT PK_WORLD_BANK_OBSERVATIONS PRIMARY KEY (
                SNAPSHOT_ID, CANONICAL_ISO3, INDICATOR_CODE, OBSERVATION_YEAR
            ) NOT ENFORCED
        )
        """)
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {TARGET_DATABASE}.{RAW_SCHEMA}.{SNAPSHOT_TABLE} (
            SNAPSHOT_ID VARCHAR NOT NULL,
            SOURCE_ID NUMBER NOT NULL,
            OBSERVATION_CHECKSUM VARCHAR NOT NULL,
            FILE_CHECKSUM VARCHAR NOT NULL,
            RETRIEVED_AT TIMESTAMP_TZ NOT NULL,
            SOURCE_LAST_UPDATED VARCHAR,
            ROW_COUNT NUMBER NOT NULL,
            COUNTRY_COUNT NUMBER NOT NULL,
            NULL_VALUE_COUNT NUMBER NOT NULL,
            PUBLICATION_STATUS VARCHAR NOT NULL,
            PUBLISHED_AT TIMESTAMP_TZ,
            IS_ACTIVE BOOLEAN NOT NULL,
            PREVIOUS_SNAPSHOT_ID VARCHAR,
            CONSTRAINT PK_WORLD_BANK_SNAPSHOTS PRIMARY KEY (SNAPSHOT_ID) NOT ENFORCED
        )
        """)


def publish_snapshot(
    connection: snowflake.connector.SnowflakeConnection,
    csv_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    rows, manifest = load_snapshot(csv_path, manifest_path)
    frame = pd.DataFrame(rows)
    frame["INDICATOR_VALUE"] = frame["INDICATOR_VALUE"].map(
        lambda value: None if value is None else Decimal(str(value))
    )
    frame["IS_AGGREGATE"] = frame["IS_AGGREGATE"].map(
        lambda value: str(value).lower() == "true"
    )
    staging = f"{OBSERVATION_TABLE}_CANDIDATE_{secrets.token_hex(6).upper()}"
    cursor = connection.cursor()
    previous_snapshot_id: str | None = None
    try:
        _ensure_tables(cursor)
        cursor.execute(
            f"SELECT SNAPSHOT_ID FROM {TARGET_DATABASE}.{RAW_SCHEMA}.{SNAPSHOT_TABLE} WHERE IS_ACTIVE"
        )
        active = [str(row[0]) for row in cursor.fetchall()]
        if len(active) > 1:
            raise WorldBankValidationError("More than one WDI snapshot is active.")
        previous_snapshot_id = active[0] if active else None
        cursor.execute(
            f"SELECT COUNT(*) FROM {TARGET_DATABASE}.{RAW_SCHEMA}.{SNAPSHOT_TABLE} WHERE SNAPSHOT_ID = %s",
            (manifest["snapshot_id"],),
        )
        if int(cursor.fetchone()[0]):
            if manifest["snapshot_id"] == previous_snapshot_id:
                return manifest
            raise WorldBankValidationError("The immutable snapshot ID already exists.")
        cursor.execute(
            f"CREATE TEMPORARY TABLE {TARGET_DATABASE}.{RAW_SCHEMA}.{staging} LIKE "
            f"{TARGET_DATABASE}.{RAW_SCHEMA}.{OBSERVATION_TABLE}"
        )
        success, _, loaded, _ = write_pandas(
            connection,
            frame,
            table_name=staging,
            database=TARGET_DATABASE,
            schema=RAW_SCHEMA,
            quote_identifiers=False,
        )
        if not success or int(loaded) != len(frame):
            raise WorldBankValidationError("The WDI candidate load was incomplete.")
        cursor.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT SNAPSHOT_ID, CANONICAL_ISO3, INDICATOR_CODE, OBSERVATION_YEAR
                FROM {TARGET_DATABASE}.{RAW_SCHEMA}.{staging}
                GROUP BY ALL HAVING COUNT(*) > 1
            )
            """)
        if int(cursor.fetchone()[0]):
            raise WorldBankValidationError(
                "Snowflake detected duplicate candidate keys."
            )
        connection.autocommit(False)
        cursor.execute(
            f"INSERT INTO {TARGET_DATABASE}.{RAW_SCHEMA}.{OBSERVATION_TABLE} SELECT * FROM {TARGET_DATABASE}.{RAW_SCHEMA}.{staging}"
        )
        cursor.execute(
            f"SELECT COUNT(*) FROM {TARGET_DATABASE}.{RAW_SCHEMA}.{OBSERVATION_TABLE} WHERE SNAPSHOT_ID=%s",
            (manifest["snapshot_id"],),
        )
        if int(cursor.fetchone()[0]) != int(manifest["row_count"]):
            raise WorldBankValidationError(
                "The inserted WDI snapshot row count does not match its manifest."
            )
        cursor.execute(
            f"""
            INSERT INTO {TARGET_DATABASE}.{RAW_SCHEMA}.{SNAPSHOT_TABLE} VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                'CANDIDATE', NULL, FALSE, %s
            )
            """,
            (
                manifest["snapshot_id"],
                SOURCE_ID,
                manifest["observation_checksum"],
                manifest["file_checksum"],
                manifest["retrieved_at"],
                manifest["source_last_updated"],
                manifest["row_count"],
                manifest["country_count"],
                manifest["null_value_count"],
                previous_snapshot_id,
            ),
        )
        connection.commit()
        connection.autocommit(False)
        if previous_snapshot_id:
            cursor.execute(
                f"UPDATE {TARGET_DATABASE}.{RAW_SCHEMA}.{SNAPSHOT_TABLE} SET PUBLICATION_STATUS='SUPERSEDED', IS_ACTIVE=FALSE WHERE SNAPSHOT_ID=%s AND IS_ACTIVE",
                (previous_snapshot_id,),
            )
        cursor.execute(
            f"UPDATE {TARGET_DATABASE}.{RAW_SCHEMA}.{SNAPSHOT_TABLE} SET PUBLICATION_STATUS='ACTIVE', IS_ACTIVE=TRUE, PUBLISHED_AT=CURRENT_TIMESTAMP() WHERE SNAPSHOT_ID=%s AND NOT IS_ACTIVE",
            (manifest["snapshot_id"],),
        )
        cursor.execute(
            f"SELECT COUNT(*) FROM {TARGET_DATABASE}.{RAW_SCHEMA}.{SNAPSHOT_TABLE} WHERE IS_ACTIVE"
        )
        if int(cursor.fetchone()[0]) != 1:
            raise WorldBankValidationError(
                "Activation did not leave exactly one active snapshot."
            )
        connection.commit()
        return manifest
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.autocommit(True)
        cursor.execute(f"DROP TABLE IF EXISTS {TARGET_DATABASE}.{RAW_SCHEMA}.{staging}")
        cursor.close()


def rollback_snapshot(
    connection: snowflake.connector.SnowflakeConnection,
    snapshot_id: str,
) -> str | None:
    """Reactivate the recorded predecessor after downstream verification fails."""
    cursor = connection.cursor()
    try:
        cursor.execute(
            f"SELECT PREVIOUS_SNAPSHOT_ID FROM {TARGET_DATABASE}.{RAW_SCHEMA}.{SNAPSHOT_TABLE} WHERE SNAPSHOT_ID=%s",
            (snapshot_id,),
        )
        row = cursor.fetchone()
        previous_snapshot_id = str(row[0]) if row and row[0] else None
        if not previous_snapshot_id:
            return None
        connection.autocommit(False)
        cursor.execute(
            f"UPDATE {TARGET_DATABASE}.{RAW_SCHEMA}.{SNAPSHOT_TABLE} SET PUBLICATION_STATUS='ROLLED_BACK', IS_ACTIVE=FALSE WHERE SNAPSHOT_ID=%s",
            (snapshot_id,),
        )
        cursor.execute(
            f"UPDATE {TARGET_DATABASE}.{RAW_SCHEMA}.{SNAPSHOT_TABLE} SET PUBLICATION_STATUS='ACTIVE', IS_ACTIVE=TRUE, PUBLISHED_AT=CURRENT_TIMESTAMP() WHERE SNAPSHOT_ID=%s",
            (previous_snapshot_id,),
        )
        cursor.execute(
            f"SELECT COUNT(*) FROM {TARGET_DATABASE}.{RAW_SCHEMA}.{SNAPSHOT_TABLE} WHERE IS_ACTIVE"
        )
        if int(cursor.fetchone()[0]) != 1:
            raise WorldBankValidationError(
                "Rollback did not restore one active snapshot."
            )
        connection.commit()
        return previous_snapshot_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.autocommit(True)
        cursor.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh or publish versioned WDI context."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    refresh = subparsers.add_parser("refresh")
    refresh.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH)
    refresh.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    refresh.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING_PATH)
    publish = subparsers.add_parser("publish")
    publish.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH)
    publish.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    publish.add_argument("--validate-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "refresh":
        manifest = refresh_snapshot(args.csv, args.manifest, args.mapping)
    else:
        rows, manifest = load_snapshot(args.csv, args.manifest)
        if not args.validate_only:
            load_dotenv()
            connection = connect_to_snowflake()
            try:
                manifest = publish_snapshot(connection, args.csv, args.manifest)
            finally:
                connection.close()
        else:
            manifest = {**manifest, "validated_rows": len(rows)}
    sys.stdout.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
