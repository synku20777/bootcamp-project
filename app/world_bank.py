from __future__ import annotations

import csv
import hashlib
import io
import json
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

API_VERSION = "v2"
SOURCE_ID = 2
SOURCE_NAME = "World Development Indicators"
OBSERVATION_YEARS = (2019, 2020, 2021)
NULL_TOKEN = r"\N"


@dataclass(frozen=True)
class IndicatorDefinition:
    code: str
    name: str
    unit: str
    minimum_coverage_pct: Decimal
    maximum_drop_pct_points: Decimal


INDICATORS: tuple[IndicatorDefinition, ...] = (
    IndicatorDefinition(
        "SP.POP.TOTL", "Population, total", "people", Decimal("95"), Decimal("5")
    ),
    IndicatorDefinition(
        "EN.POP.DNST",
        "Population density (people per sq. km of land area)",
        "people per sq. km of land area",
        Decimal("90"),
        Decimal("5"),
    ),
    IndicatorDefinition(
        "SP.POP.65UP.TO.ZS",
        "Population ages 65 and above (% of total population)",
        "% of total population",
        Decimal("90"),
        Decimal("5"),
    ),
    IndicatorDefinition(
        "NY.GDP.PCAP.KD",
        "GDP per capita (constant 2015 US$)",
        "constant 2015 US$",
        Decimal("80"),
        Decimal("5"),
    ),
    IndicatorDefinition(
        "SH.XPD.CHEX.PP.CD",
        "Current health expenditure per capita, PPP (current international $)",
        "current international $",
        Decimal("75"),
        Decimal("10"),
    ),
)
INDICATOR_BY_CODE = {definition.code: definition for definition in INDICATORS}
INDICATOR_CODES = tuple(INDICATOR_BY_CODE)

OBSERVATION_CHECKSUM_COLUMNS = (
    "CANONICAL_ISO2",
    "CANONICAL_ISO3",
    "COUNTRY_NAME",
    "INDICATOR_CODE",
    "INDICATOR_NAME",
    "INDICATOR_UNIT",
    "SOURCE_ID",
    "SOURCE_NAME",
    "OBSERVATION_YEAR",
    "INDICATOR_VALUE",
    "OBSERVATION_STATUS",
    "SOURCE_DECIMAL_PRECISION",
    "SOURCE_LAST_UPDATED",
)

SNAPSHOT_COLUMNS = (
    "SNAPSHOT_ID",
    "SOURCE_ENTITY_CODE",
    "CANONICAL_ISO2",
    "CANONICAL_ISO3",
    "COUNTRY_NAME",
    "CODE_SYSTEM",
    "ENTITY_TYPE",
    "IS_AGGREGATE",
    "IDENTITY_MAPPING_STATUS",
    "INDICATOR_CODE",
    "INDICATOR_NAME",
    "INDICATOR_UNIT",
    "SOURCE_ID",
    "SOURCE_NAME",
    "OBSERVATION_YEAR",
    "INDICATOR_VALUE",
    "SOURCE_LAST_UPDATED",
    "OBSERVATION_STATUS",
    "SOURCE_DECIMAL_PRECISION",
    "RAW_VALUE_TEXT",
    "RETRIEVED_AT",
)


class WorldBankValidationError(RuntimeError):
    """A candidate snapshot violates a publication invariant."""


def normalize_text(value: Any) -> str:
    if value is None:
        return NULL_TOKEN
    return unicodedata.normalize("NFC", str(value).strip())


def normalize_decimal(value: Any) -> str:
    if value is None or normalize_text(value) in {"", NULL_TOKEN}:
        return NULL_TOKEN
    try:
        decimal_value = Decimal(str(value))
    except InvalidOperation as exc:
        raise WorldBankValidationError(f"Invalid decimal value: {value!r}") from exc
    if not decimal_value.is_finite():
        raise WorldBankValidationError(f"Non-finite decimal value: {value!r}")
    quantum = Decimal("0.000000001")
    decimal_value = decimal_value.quantize(quantum, rounding=ROUND_HALF_EVEN)
    if decimal_value == 0:
        return "0"
    return format(decimal_value, "f").rstrip("0").rstrip(".")


def canonical_observation_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    ordered = sorted(
        rows,
        key=lambda row: (
            normalize_text(row.get("CANONICAL_ISO3")),
            normalize_text(row.get("INDICATOR_CODE")),
            int(row.get("OBSERVATION_YEAR")),
        ),
    )
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(OBSERVATION_CHECKSUM_COLUMNS)
    for row in ordered:
        values: list[str] = []
        for column in OBSERVATION_CHECKSUM_COLUMNS:
            value = row.get(column)
            if column == "INDICATOR_VALUE":
                values.append(normalize_decimal(value))
            else:
                normalized = normalize_text(value)
                values.append(normalized if normalized else NULL_TOKEN)
        writer.writerow(values)
    return stream.getvalue().encode("utf-8")


def observation_checksum(rows: Iterable[Mapping[str, Any]]) -> str:
    return hashlib.sha256(canonical_observation_bytes(rows)).hexdigest()


def snapshot_csv_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(SNAPSHOT_COLUMNS)
    for row in sorted(
        rows,
        key=lambda item: (
            normalize_text(item.get("CANONICAL_ISO3")),
            normalize_text(item.get("INDICATOR_CODE")),
            int(item.get("OBSERVATION_YEAR")),
        ),
    ):
        values = []
        for column in SNAPSHOT_COLUMNS:
            value = row.get(column)
            if column == "INDICATOR_VALUE":
                value = normalize_decimal(value)
            else:
                value = normalize_text(value)
            values.append(value if value else NULL_TOKEN)
        writer.writerow(values)
    return stream.getvalue().encode("utf-8")


def load_snapshot(
    csv_path: Path, manifest_path: Path
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    try:
        file_bytes = csv_path.read_bytes()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorldBankValidationError(
            "The committed WDI snapshot is unreadable."
        ) from exc
    if hashlib.sha256(file_bytes).hexdigest() != manifest.get("file_checksum"):
        raise WorldBankValidationError(
            "The WDI file checksum does not match its manifest."
        )
    text = file_bytes.decode("utf-8")
    if not text.endswith("\n") or "\r\n" in text:
        raise WorldBankValidationError("The WDI snapshot is not canonical LF text.")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if tuple(reader.fieldnames or ()) != SNAPSHOT_COLUMNS:
        raise WorldBankValidationError(
            "The WDI snapshot columns do not match the contract."
        )
    rows = [dict(row) for row in reader]
    for row in rows:
        for column, value in tuple(row.items()):
            if value == NULL_TOKEN:
                row[column] = None  # type: ignore[assignment]
    validate_candidate(rows)
    if observation_checksum(rows) != manifest.get("observation_checksum"):
        raise WorldBankValidationError(
            "The logical WDI observation checksum does not match its manifest."
        )
    if len(rows) != manifest.get("row_count"):
        raise WorldBankValidationError("The WDI row count does not match its manifest.")
    snapshot_ids = {row.get("SNAPSHOT_ID") for row in rows}
    if snapshot_ids != {manifest.get("snapshot_id")}:
        raise WorldBankValidationError("Snapshot IDs differ between data and manifest.")
    return rows, manifest


def duplicate_report(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    for row in rows:
        key = (
            row.get("SNAPSHOT_ID"),
            row.get("CANONICAL_ISO3"),
            row.get("INDICATOR_CODE"),
            int(row.get("OBSERVATION_YEAR")),
        )
        groups.setdefault(key, []).append(row)
    exact: list[dict[str, Any]] = []
    conflicting: list[dict[str, Any]] = []
    for key, records in groups.items():
        if len(records) < 2:
            continue
        normalized_values = {
            (
                normalize_decimal(record.get("INDICATOR_VALUE")),
                normalize_text(record.get("OBSERVATION_STATUS")),
            )
            for record in records
        }
        target = exact if len(normalized_values) == 1 else conflicting
        target.append({"key": list(key), "row_count": len(records)})
    return {"exact": exact, "conflicting": conflicting}


def validate_candidate(rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        raise WorldBankValidationError("The WDI candidate is empty.")
    duplicates = duplicate_report(rows)
    if duplicates["exact"] or duplicates["conflicting"]:
        raise WorldBankValidationError("The WDI candidate contains duplicate keys.")
    snapshot_ids = {row.get("SNAPSHOT_ID") for row in rows}
    if len(snapshot_ids) != 1 or None in snapshot_ids:
        raise WorldBankValidationError(
            "A candidate must contain exactly one snapshot ID."
        )
    allowed_statuses = {"matched_iso", "matched_explicit_mapping"}
    for row in rows:
        if row.get("IDENTITY_MAPPING_STATUS") == "conflicting_identity":
            raise WorldBankValidationError("A conflicting identity blocks publication.")
        if row.get("IDENTITY_MAPPING_STATUS") not in allowed_statuses:
            raise WorldBankValidationError(
                "Only canonical identities may be published."
            )
        if row.get("INDICATOR_CODE") not in INDICATOR_BY_CODE:
            raise WorldBankValidationError("An unexpected WDI indicator was accepted.")
        if int(row.get("OBSERVATION_YEAR")) not in OBSERVATION_YEARS:
            raise WorldBankValidationError("A WDI observation year is out of scope.")
        iso3 = normalize_text(row.get("CANONICAL_ISO3"))
        if len(iso3) != 3:
            raise WorldBankValidationError("A canonical ISO3 value is invalid.")
        value = row.get("INDICATOR_VALUE")
        if value is not None:
            normalize_decimal(value)
