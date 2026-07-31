from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path

from app.spark_pipeline.schemas import DATASETS

WORLD_BANK_SNAPSHOT_ID = "wdi2-fixture"

_VALID_ROWS = {
    "ecdc": ("Latvia", "LV", "2020-03-01", "1", "0"),
    "mapping": ("Latvia", "LV", "Latvia", "LV", "LVA", "true"),
    "population": ("LV", "LVA", "Latvia", "1900000", "2020"),
    "indicators": (
        WORLD_BANK_SNAPSHOT_ID,
        "LVA",
        "LV",
        "LVA",
        "Latvia",
        "ISO-3166",
        "economy",
        "false",
        "matched_iso",
        "SP.POP.TOTL",
        "Population total",
        "people",
        "2",
        "World Development Indicators",
        "2020",
        "1900000.000000000",
        "2026-07-01",
        "available",
        "0",
        "1900000",
        "2026-07-29T12:00:00Z",
    ),
    "covid_extended": (
        "Latvia",
        "LV",
        "LVA",
        "LVA",
        "2020-03-01",
        "1",
        "0",
        "1",
        "0",
        "1900000",
        "0.052632",
        "0.0",
        "0.052632",
        "0.0",
        "false",
        "false",
        "ECDC",
        "ECDC_BASELINE",
    ),
}


def _csv_document(headers: Iterable[str], row: Iterable[str]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(headers)
    writer.writerow(row)
    return stream.getvalue()


def _csv_rows_document(headers: Iterable[str], rows: Iterable[Iterable[object]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return stream.getvalue()


def write_source_batch(
    source_directory: Path,
    *,
    ecdc_header: str | None = None,
    ecdc_row: str | None = None,
    missing_files: frozenset[str] = frozenset(),
    undeclared_files: frozenset[str] = frozenset(),
) -> dict[str, object]:
    """Create a complete source contract, then apply only the requested defect."""
    source_directory.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict[str, object]] = {}
    written_paths: dict[str, Path] = {}

    for dataset in DATASETS:
        headers = dataset.headers
        row = _VALID_ROWS[dataset.name]
        if dataset.name == "ecdc" and ecdc_header is not None:
            headers = tuple(ecdc_header.split(","))
        if dataset.name == "ecdc" and ecdc_row is not None:
            row = tuple(ecdc_row.split(","))

        path = source_directory / dataset.filename
        path.write_text(_csv_document(headers, row), encoding="utf-8")
        written_paths[dataset.name] = path
        files[dataset.name] = {
            "filename": dataset.filename,
            "row_count": 1,
            "byte_count": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    for dataset_name in undeclared_files:
        files.pop(dataset_name)

    checksum_payload = "".join(str(files[name]["sha256"]) for name in sorted(files))
    manifest: dict[str, object] = {
        "manifest_version": 3,
        "source_kind": "fixture",
        "source_batch_id": source_directory.name,
        "extracted_at_utc": "2026-07-29T12:00:00Z",
        "sources": {dataset.name: f"fixture:{dataset.name}" for dataset in DATASETS},
        "world_bank_snapshot_id": WORLD_BANK_SNAPSHOT_ID,
        "snowflake_context_fingerprint": {
            "protocol": "country-context-v1",
            "row_count": 1,
            "sha256": "a" * 64,
        },
        "files": files,
        "batch_sha256": hashlib.sha256(checksum_payload.encode("ascii")).hexdigest(),
    }
    (source_directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    # Files are removed after checksums are captured so declared-missing and
    # undeclared-missing scenarios remain faithful to their distinct contracts.
    for dataset_name in missing_files | undeclared_files:
        written_paths[dataset_name].unlink()

    return manifest


def write_pipeline_source_batch(source_directory: Path) -> dict[str, object]:
    """Create a deterministic six-country batch for the full offline workflow."""
    start = date(2020, 1, 1)
    ecdc_rows: list[tuple[object, ...]] = []
    mapping_rows: list[tuple[object, ...]] = []
    population_rows: list[tuple[object, ...]] = []
    indicator_rows: list[tuple[object, ...]] = []
    extended_rows: list[tuple[object, ...]] = []
    indicators = (
        ("SP.POP.TOTL", 2020, 100_000.0),
        ("EN.POP.DNST", 2019, 10.0),
        ("SP.POP.65UP.TO.ZS", 2019, 15.0),
        ("NY.GDP.PCAP.KD", 2019, 20_000.0),
        ("SH.XPD.CHEX.PP.CD", 2019, 2_000.0),
    )
    for country_index in range(6):
        country = f"Country {country_index}"
        iso2 = f"Q{country_index}"
        iso3 = f"X{country_index:02d}"
        high_burden = country_index >= 3
        mapping_rows.append((country, iso2, country, iso2, iso3, True))
        population_rows.append((iso2, iso3, country, 100_000, 2020))
        for indicator_code, year, base_value in indicators:
            value = base_value + country_index
            indicator_rows.append(
                (
                    WORLD_BANK_SNAPSHOT_ID,
                    iso3,
                    iso2,
                    iso3,
                    country,
                    "ISO-3166",
                    "economy",
                    False,
                    "matched_iso",
                    indicator_code,
                    indicator_code,
                    "fixture-unit",
                    2,
                    "World Development Indicators",
                    year,
                    value,
                    "2026-07-01",
                    "available",
                    0,
                    value,
                    "2026-07-29T12:00:00Z",
                )
            )
        cumulative_cases = 0
        cumulative_deaths = 0
        for offset in range(20):
            report_date = start + timedelta(days=offset)
            cases = 30 + country_index + offset % 3 if high_burden else 1
            deaths = 3 + offset % 2 if high_burden else 0
            if country_index == 0 and offset == 5:
                cases = -2
            cumulative_cases += cases
            cumulative_deaths += deaths
            ecdc_rows.append((country, iso2, report_date, cases, deaths))
            extended_rows.append(
                (
                    country,
                    iso2,
                    iso3,
                    iso3,
                    report_date,
                    cases,
                    deaths,
                    cumulative_cases,
                    cumulative_deaths,
                    100_000,
                    float(cases),
                    float(deaths),
                    float(cumulative_cases),
                    float(cumulative_deaths),
                    cases < 0,
                    False,
                    "JHU",
                    "JHU_ONLY",
                )
            )

    rows_by_dataset = {
        "ecdc": ecdc_rows,
        "mapping": mapping_rows,
        "population": population_rows,
        "indicators": indicator_rows,
        "covid_extended": extended_rows,
    }
    source_directory.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict[str, object]] = {}
    for dataset in DATASETS:
        rows = rows_by_dataset[dataset.name]
        path = source_directory / dataset.filename
        path.write_text(
            _csv_rows_document(dataset.headers, rows),
            encoding="utf-8",
        )
        entry: dict[str, object] = {
            "filename": dataset.filename,
            "row_count": len(rows),
            "byte_count": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        if dataset.name == "covid_extended":
            entry.update(
                {
                    "minimum_report_date": start.isoformat(),
                    "maximum_report_date": (start + timedelta(days=19)).isoformat(),
                    "country_count": 6,
                }
            )
        files[dataset.name] = entry

    checksum_payload = "".join(str(files[name]["sha256"]) for name in sorted(files))
    manifest: dict[str, object] = {
        "manifest_version": 3,
        "source_kind": "fixture",
        "source_batch_id": source_directory.name,
        "extracted_at_utc": "2026-07-29T12:00:00Z",
        "sources": {dataset.name: f"fixture:{dataset.name}" for dataset in DATASETS},
        "world_bank_snapshot_id": WORLD_BANK_SNAPSHOT_ID,
        "snowflake_context_fingerprint": {},
        "files": files,
        "batch_sha256": hashlib.sha256(checksum_payload.encode("ascii")).hexdigest(),
    }
    (source_directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest
