"""Pre-publication source validation, ingestion reads, and quality metadata.

This boundary owns all source-facing contracts.  File and header checks run
before a SparkSession is created so invalid input fails cheaply and cannot
leave a partial Bronze publication.  Once validation succeeds, this module
also constructs the schema-bound Bronze DataFrames and their source profiles;
it never owns durable publication or the SparkSession lifecycle.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pyspark.sql import DataFrame, SparkSession

from app.spark_pipeline.contracts import SparkPipelineError
from app.spark_pipeline.evidence import _document_sha256
from app.spark_pipeline.quality import (
    QUALITY_RULESET_VERSION,
    inspect_header,
    profile_dataset,
    read_bronze_source,
)
from app.spark_pipeline.schemas import DATASETS


def _file_sha256(path: Path) -> str:
    """Hash a source file incrementally without loading it into driver memory."""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validation_check(
    rule: str,
    *,
    passed: bool,
    message: str,
    dataset: str | None = None,
    expected_path: str | None = None,
) -> dict[str, Any]:
    """Build the stable quality-check shape used by pre-Spark validation."""

    check: dict[str, Any] = {
        "rule": rule,
        "severity": "FAIL",
        "count": 0 if passed else 1,
        "passed": passed,
        "message": message,
    }
    if dataset is not None:
        check["dataset"] = dataset
    if expected_path is not None:
        check["expected_path"] = expected_path
    return check


def _inspect_source_manifest(
    source_directory: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read the batch manifest and report structural contract violations."""

    manifest_path = source_directory / "manifest.json"
    present = manifest_path.is_file()
    checks = [
        _validation_check(
            "source_manifest_present",
            passed=present,
            message="Every source batch requires a manifest before Spark starts.",
            dataset="manifest",
            expected_path=manifest_path.name,
        )
    ]
    if not present:
        return {}, checks

    try:
        candidate = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        checks.append(
            _validation_check(
                "source_manifest_valid",
                passed=False,
                message="The source manifest must be readable, valid JSON.",
                dataset="manifest",
                expected_path=manifest_path.name,
            )
        )
        return {}, checks

    manifest = candidate if isinstance(candidate, dict) else {}
    valid = (
        manifest.get("manifest_version") == 3
        and manifest.get("source_kind") in {"snowflake_export", "fixture"}
        and manifest.get("source_batch_id") == source_directory.name
        and isinstance(manifest.get("files"), dict)
        and isinstance(manifest.get("batch_sha256"), str)
        and isinstance(manifest.get("world_bank_snapshot_id"), str)
        and isinstance(manifest.get("snowflake_context_fingerprint"), dict)
    )
    checks.append(
        _validation_check(
            "source_manifest_valid",
            passed=valid,
            message=(
                "The source manifest must use version 3 and include batch, file, "
                "and World Bank context metadata."
            ),
            dataset="manifest",
            expected_path=manifest_path.name,
        )
    )
    return manifest, checks


def _source_validation_results(
    source_directory: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate manifest lineage, required files, and immutable checksums."""

    manifest, checks = _inspect_source_manifest(source_directory)
    files = manifest.get("files")
    manifest_files = files if isinstance(files, dict) else {}
    batch_sha256 = manifest.get("batch_sha256")
    if isinstance(files, dict) and isinstance(batch_sha256, str):
        file_checksums: list[str] = []
        checksum_material_valid = True
        for _, details in sorted(files.items()):
            checksum = details.get("sha256") if isinstance(details, dict) else None
            if not isinstance(checksum, str):
                checksum_material_valid = False
                break
            file_checksums.append(checksum)
        calculated_batch_sha256 = (
            hashlib.sha256("".join(file_checksums).encode("ascii")).hexdigest()
            if checksum_material_valid
            else None
        )
        checks.append(
            _validation_check(
                "source_batch_checksum_matches",
                passed=calculated_batch_sha256 == batch_sha256,
                message=(
                    "The batch checksum must match the ordered source-file "
                    "checksums recorded in the manifest."
                ),
                dataset="manifest",
                expected_path="manifest.json",
            )
        )

    for dataset in DATASETS:
        details = manifest_files.get(dataset.name)
        declared = (
            isinstance(details, dict) and details.get("filename") == dataset.filename
        )
        checks.append(
            _validation_check(
                "required_source_declared",
                passed=declared,
                message=(
                    "Every registered Spark source must use its versioned filename "
                    "in the batch manifest."
                ),
                dataset=dataset.name,
                expected_path=dataset.filename,
            )
        )

        source_path = source_directory / dataset.filename
        present = source_path.is_file()
        checks.append(
            _validation_check(
                "required_source_file_present",
                passed=present,
                message="Every registered Spark source file is mandatory.",
                dataset=dataset.name,
                expected_path=dataset.filename,
            )
        )

        if declared and present:
            expected_checksum = details.get("sha256")
            try:
                checksum_matches = (
                    isinstance(expected_checksum, str)
                    and _file_sha256(source_path) == expected_checksum
                )
            except OSError:
                checksum_matches = False
            checks.append(
                _validation_check(
                    "source_file_checksum_matches",
                    passed=checksum_matches,
                    message=(
                        "Source bytes must match the immutable manifest before "
                        "schema inspection or Spark ingestion."
                    ),
                    dataset=dataset.name,
                    expected_path=dataset.filename,
                )
            )

    return manifest, checks


def _header_results(source_directory: Path) -> dict[str, dict[str, Any]]:
    """Inspect every registered CSV header without initializing Spark."""

    return {
        dataset.name: inspect_header(
            source_directory / dataset.filename,
            dataset.headers,
        )
        for dataset in DATASETS
    }


def _schema_checks(headers: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate readable header comparisons into quality-rule results."""

    return [
        {
            "rule": f"{name}.schema_exact",
            "severity": "FAIL",
            "count": 0 if result["matches"] else 1,
            "passed": result["matches"],
            "message": "CSV header must exactly match the versioned contract.",
        }
        for name, result in headers.items()
        if result["present"] and result["readable"]
    ]


def _source_readability_checks(
    headers: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Report files that exist but cannot be read by the current process."""

    return [
        _validation_check(
            "source_file_readable",
            passed=result["readable"],
            message="Present source files must be readable before Spark starts.",
            dataset=name,
            expected_path=result["expected_path"],
        )
        for name, result in headers.items()
        if result["present"]
    ]


def _pre_spark_failure_quality(
    *,
    headers: dict[str, dict[str, Any]],
    source_batch_id: str,
    source_batch_sha256: str | None,
    ingestion_id: str,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create a complete failure document when Spark never starts."""

    return _quality_document(
        assessment={
            "ruleset_version": QUALITY_RULESET_VERSION,
            "status": "FAIL",
            "checks": checks,
        },
        source_batch_id=source_batch_id,
        source_batch_sha256=source_batch_sha256,
        ingestion_id=ingestion_id,
        headers=headers,
        profiles={},
        publication={"bronze": False, "curated": False},
    )


def _quality_document(
    *,
    assessment: dict[str, Any],
    source_batch_id: str,
    source_batch_sha256: str | None,
    ingestion_id: str,
    headers: dict[str, dict[str, Any]],
    profiles: dict[str, dict[str, Any]],
    publication: dict[str, bool],
) -> dict[str, Any]:
    """Attach immutable lineage and publication state to a quality assessment."""

    return {
        **assessment,
        "ruleset_version": QUALITY_RULESET_VERSION,
        "source_batch_id": source_batch_id,
        "source_batch_sha256": source_batch_sha256,
        "ingestion_id": ingestion_id,
        "headers": headers,
        "profiles": profiles,
        "publication": publication,
    }


def _quality_summary(quality: dict[str, Any]) -> dict[str, str]:
    """Reduce a quality document to the checksum-backed Bronze manifest contract."""

    return {
        "ruleset_version": str(quality["ruleset_version"]),
        "status": str(quality["status"]),
        "quality_document_sha256": _document_sha256(quality),
    }


def _read_sources(
    spark: SparkSession,
    source_directory: Path,
    *,
    source_batch_id: str,
    ingestion_id: str,
    ingested_at: datetime,
) -> dict[str, DataFrame]:
    """Read schema-bound sources and materialize their persisted parse results.

    ``read_bronze_source`` persists each DataFrame.  The explicit counts are
    therefore intentional Spark actions: malformed-row parsing is completed
    before profiling and later transformations reuse the materialized frames.
    Cleanup remains the execution harness's responsibility.
    """

    frames = {
        dataset.name: read_bronze_source(
            spark,
            dataset,
            source_directory / dataset.filename,
            source_batch_id=source_batch_id,
            ingestion_id=ingestion_id,
            ingested_at=ingested_at,
        )
        for dataset in DATASETS
    }
    for frame in frames.values():
        frame.count()
    return frames


def _profiles(
    frames: dict[str, DataFrame],
    source_directory: Path,
    *,
    exact_distinct_max_rows: int,
) -> dict[str, dict[str, Any]]:
    """Compute per-dataset profiles from the already materialized source frames."""

    return {
        dataset.name: profile_dataset(
            frames[dataset.name],
            dataset,
            input_bytes=(source_directory / dataset.filename).stat().st_size,
            exact_distinct_max_rows=exact_distinct_max_rows,
        )
        for dataset in DATASETS
    }


def _quality_from_bronze_manifest(manifest: dict[str, Any]) -> dict[str, str]:
    """Validate and return the quality gate used by benchmark-only execution."""

    if manifest.get("manifest_version") != 3:
        raise SparkPipelineError(
            "Benchmark-only mode requires Bronze manifest version 3."
        )
    quality = manifest.get("quality_summary")
    if not isinstance(quality, dict):
        raise SparkPipelineError("Bronze manifest has no quality summary.")
    required = {"ruleset_version", "status", "quality_document_sha256"}
    if not required.issubset(quality):
        raise SparkPipelineError("Bronze quality summary is incomplete.")
    if quality["status"] not in {"PASS", "WARN"}:
        raise SparkPipelineError("Bronze quality status does not permit benchmarking.")
    return {name: str(quality[name]) for name in sorted(required)}
