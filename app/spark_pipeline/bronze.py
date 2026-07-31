"""Atomic Bronze and curated-tier persistence for the Spark pipeline.

This module owns filesystem layout and commit semantics, not transformation
semantics.  Writes go to ingestion-specific staging directories and are
renamed only after every dataset and manifest succeeds, which keeps consumers
from observing a partially published tier.  DataFrame schemas are passed
through unchanged except for the pre-existing curated partition columns.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from pyspark import StorageLevel
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from app.spark_pipeline.benchmark import directory_metrics
from app.spark_pipeline.evidence import _write_json
from app.spark_pipeline.runtime_policy import SparkRuntimePolicy
from app.spark_pipeline.schemas import CORRUPT_RECORD_COLUMN, DATASETS


def _publish_bronze(
    frames: dict[str, DataFrame],
    *,
    bronze_root: Path,
    source_batch_id: str,
    source_batch_sha256: str,
    source_files: dict[str, Any],
    ingestion_id: str,
    spark_application_id: str,
    quality_summary: dict[str, str],
    world_bank_snapshot_id: str | None,
    snowflake_context_fingerprint: dict[str, Any] | None,
    source_kind: str,
    runtime_policy: SparkRuntimePolicy,
) -> Path:
    """Publish raw parsed datasets and their lineage manifest atomically.

    Both valid and corrupt-record projections are written because Bronze is an
    auditable representation of ingestion, not a quality-filtered data mart.
    File counts are policy-driven to avoid small-file proliferation.
    """

    target = bronze_root / f"ingestion_id={ingestion_id}"
    if target.exists():
        raise FileExistsError(f"Bronze ingestion already exists: {ingestion_id}")
    staging = bronze_root / f".staging-{ingestion_id}-{uuid4().hex}"
    staging.mkdir(parents=True)
    publication_layout: dict[str, Any] = {}
    try:
        for name, frame in frames.items():
            input_bytes = int(source_files.get(name, {}).get("byte_count", 0))
            target_files = runtime_policy.target_file_count(input_bytes)
            records = _resize_for_output(frame, target_files)
            records_target = staging / name / "records"
            corrupt_target = staging / name / "corrupt_records"
            records.write.mode("error").parquet(str(records_target))
            _resize_for_output(
                frame.where(F.col(CORRUPT_RECORD_COLUMN).isNotNull()),
                target_files,
            ).write.mode("error").parquet(str(corrupt_target))
            publication_layout[name] = {
                "input_bytes": input_bytes,
                "input_partitions": frame.rdd.getNumPartitions(),
                "target_file_count": target_files,
                "records": directory_metrics(records_target),
                "corrupt_records": directory_metrics(corrupt_target),
            }
        _write_json(
            staging / "manifest.json",
            {
                "manifest_version": 3,
                "source_kind": source_kind,
                "source_batch_id": source_batch_id,
                "source_batch_sha256": source_batch_sha256,
                "source_files": source_files,
                "ingestion_id": ingestion_id,
                "spark_application_id": spark_application_id,
                "datasets": list(frames),
                "quality_summary": quality_summary,
                "world_bank_snapshot_id": world_bank_snapshot_id,
                "snowflake_context_fingerprint": snowflake_context_fingerprint,
                "runtime_policy": runtime_policy.as_dict(),
                "bronze_publication_layout": publication_layout,
            },
        )
        staging.replace(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def _resize_for_output(dataframe: DataFrame, target_partitions: int) -> DataFrame:
    """Adjust output parallelism while avoiding a shuffle when only shrinking.

    ``coalesce`` preserves existing partition placement and is cheaper when
    reducing files; ``repartition`` is required when increasing parallelism.
    Both calls remain lazy until the caller performs its write action.
    """

    current = dataframe.rdd.getNumPartitions()
    if current > target_partitions:
        return dataframe.coalesce(target_partitions)
    if current < target_partitions:
        return dataframe.repartition(target_partitions)
    return dataframe


def _read_bronze(
    spark: SparkSession, ingestion_directory: Path
) -> dict[str, DataFrame]:
    """Load and persist all Bronze record datasets for benchmark reuse.

    ``MEMORY_AND_DISK`` protects larger ingestions from executor-memory
    pressure while still avoiding repeated Parquet reads.  The pipeline
    harness unpersists these frames in its outermost cleanup block.
    """

    return {
        dataset.name: spark.read.parquet(
            str(ingestion_directory / dataset.name / "records")
        ).persist(StorageLevel.MEMORY_AND_DISK)
        for dataset in DATASETS
    }


def _write_curated(
    dataframe: DataFrame,
    *,
    curated_root: Path,
    ingestion_id: str,
    layout: dict[str, Any],
) -> dict[str, int]:
    """Publish the optimized analytical DataFrame using the calibrated layout.

    Date partition columns are added only when the existing layout decision
    requests them.  That conditional preserves the established curated schema
    and file contract for unpartitioned runs.
    """

    target = curated_root / f"ingestion_id={ingestion_id}"
    if target.exists():
        raise FileExistsError(f"Curated ingestion already exists: {ingestion_id}")
    staging = curated_root / f".staging-{ingestion_id}-{uuid4().hex}"
    output = _resize_for_output(dataframe, layout["target_file_count"])
    try:
        if layout["partition_columns"]:
            output = (
                dataframe.withColumn("report_year", F.year("report_date"))
                .withColumn("report_month", F.month("report_date"))
                .repartition(layout["target_file_count"], "report_year", "report_month")
            )
            output.write.mode("error").partitionBy(
                "report_year", "report_month"
            ).parquet(str(staging))
        else:
            output.write.mode("error").parquet(str(staging))
        metrics = directory_metrics(staging)
        staging.replace(target)
        return metrics
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
