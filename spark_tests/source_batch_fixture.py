from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Iterable
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
}


def _csv_document(headers: Iterable[str], row: Iterable[str]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(headers)
    writer.writerow(row)
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
        "manifest_version": 2,
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
