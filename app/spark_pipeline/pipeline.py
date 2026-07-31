"""Spark job entry points and lifecycle harness.

Responsibility-specific implementation lives in sibling modules.  Imports are
intentionally rebound here and listed in ``__all__`` to preserve the original
public and semi-private API, including test patch targets.  This module retains
only configuration, SparkSession ownership, stage sequencing, and CLI-facing
entry points.
"""

from __future__ import annotations

import json
import logging
import platform
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pyspark.sql import DataFrame, SparkSession

from app.spark_pipeline.benchmark import parse_event_logs, summarize_benchmarks
from app.spark_pipeline.benchmark_orchestration import (
    _aqe_action,
    _benchmark_suite,
    _cache_action,
    _cache_correctness,
    _cache_input,
    _cache_results,
    _calibrate_layout,
    _count_action,
    _country_context_equivalence,
    _duplicate_aggregation,
    _enriched_context_metrics,
    _projected_input,
    _projection_action,
    _projection_result,
    _single_output,
    _write_action,
    _write_correctness,
    _write_dataframe,
)
from app.spark_pipeline.bronze import (
    _publish_bronze,
    _read_bronze,
    _resize_for_output,
    _write_curated,
)
from app.spark_pipeline.clustering_orchestration import (
    _clustering_stage,
)
from app.spark_pipeline.contracts import (
    BenchmarkSuiteResult,
    QualityFailure,
    SourceValidationError,
    SparkPipelineError,
)
from app.spark_pipeline.evidence import (
    _conclusions,
    _document_sha256,
    _json_document,
    _publish_evidence_documents,
    _sanitized_evidence,
    _write_json,
    _write_json_atomic,
)
from app.spark_pipeline.quality import evaluate_quality
from app.spark_pipeline.runtime_policy import (
    DEFAULT_ADVISORY_PARTITION_BYTES,
    DEFAULT_BROADCAST_MAX_BYTES,
    DEFAULT_CLUSTER_K_MAX,
    DEFAULT_CLUSTER_K_MIN,
    DEFAULT_CLUSTER_MIN_OBSERVATIONS,
    DEFAULT_SKEW_RATIO_WARN,
    DEFAULT_SKEW_SHARE_WARN,
    DEFAULT_TARGET_FILE_BYTES,
    SparkRuntimePolicy,
)
from app.spark_pipeline.source_validation import (
    _file_sha256,
    _header_results,
    _inspect_source_manifest,
    _pre_spark_failure_quality,
    _profiles,
    _quality_document,
    _quality_from_bronze_manifest,
    _quality_summary,
    _read_sources,
    _schema_checks,
    _source_readability_checks,
    _source_validation_results,
    _validation_check,
)
from app.spark_pipeline.transformations import duplicate_count, normalized_daily

__all__ = [
    "SparkPipelineError",
    "QualityFailure",
    "SourceValidationError",
    "BenchmarkSuiteResult",
    "_write_json",
    "_json_document",
    "_document_sha256",
    "_write_json_atomic",
    "_file_sha256",
    "_validation_check",
    "_inspect_source_manifest",
    "_source_validation_results",
    "create_spark_session",
    "_release_spark_resources",
    "_header_results",
    "_schema_checks",
    "_source_readability_checks",
    "_pre_spark_failure_quality",
    "_quality_document",
    "_quality_summary",
    "_read_sources",
    "_profiles",
    "_publish_bronze",
    "_resize_for_output",
    "_read_bronze",
    "_count_action",
    "_single_output",
    "_country_context_equivalence",
    "_enriched_context_metrics",
    "_projected_input",
    "_projection_result",
    "_projection_action",
    "_duplicate_aggregation",
    "_aqe_action",
    "_cache_input",
    "_cache_results",
    "_cache_action",
    "_cache_correctness",
    "_write_dataframe",
    "_write_action",
    "_write_correctness",
    "_calibrate_layout",
    "_benchmark_suite",
    "_write_curated",
    "_environment",
    "_conclusions",
    "_sanitized_evidence",
    "_quality_from_bronze_manifest",
    "_policy_from_manifest",
    "_clustering_stage",
    "_publish_evidence_documents",
    "run_ingest_profile",
    "run_benchmark",
]

logger = logging.getLogger(__name__)


def create_spark_session(
    *,
    application_name: str,
    event_log_directory: Path,
    policy: SparkRuntimePolicy,
) -> SparkSession:
    """Create the policy-configured local SparkSession used by pipeline jobs."""

    event_log_directory.mkdir(parents=True, exist_ok=True)
    spark = (
        SparkSession.builder.master("local[2]")
        .appName(application_name)
        .config("spark.ui.enabled", "false")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
        .config(
            "spark.sql.adaptive.advisoryPartitionSizeInBytes",
            policy.advisory_partition_bytes,
        )
        .config("spark.sql.shuffle.partitions", policy.shuffle_partitions)
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.autoBroadcastJoinThreshold", -1)
        .config("spark.eventLog.enabled", "true")
        .config("spark.eventLog.dir", event_log_directory.resolve().as_uri())
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    return spark


def _release_spark_resources(
    spark: SparkSession,
    frames: dict[str, DataFrame],
) -> None:
    """Release persisted frames, catalog cache entries, and the SparkSession.

    Nested ``finally`` blocks ensure a failure in one cleanup layer cannot
    prevent JVM/session shutdown.  Blocking unpersist is intentional at the
    terminal lifecycle boundary so cached executor data is released before
    ``stop`` returns.
    """

    try:
        for frame in frames.values():
            frame.unpersist(blocking=True)
    finally:
        try:
            spark.catalog.clearCache()
        finally:
            # PySpark 3.5 clears its JVM and Python active/default session
            # registries inside stop(); keeping it in the outermost finally also
            # covers failures during DataFrame or cache cleanup.
            spark.stop()


def _environment(
    spark: SparkSession,
    policy: SparkRuntimePolicy,
) -> dict[str, Any]:
    """Capture reproducibility metadata from the active Spark runtime."""

    return {
        "python": platform.python_version(),
        "python_image": "python:3.12.13-slim-bookworm",
        "pyspark": spark.version,
        "java": spark.sparkContext._jvm.java.lang.System.getProperty("java.version"),
        "java_image": "eclipse-temurin:17.0.19_10-jre-jammy",
        "uv": "0.11.29",
        "master": spark.sparkContext.master,
        "spark_application_id": spark.sparkContext.applicationId,
        "adaptive_enabled": spark.conf.get("spark.sql.adaptive.enabled"),
        "shuffle_partitions": spark.conf.get("spark.sql.shuffle.partitions"),
        "advisory_partition_bytes": spark.conf.get(
            "spark.sql.adaptive.advisoryPartitionSizeInBytes"
        ),
        "runtime_policy": policy.as_dict(),
    }


def _policy_from_manifest(
    manifest: dict[str, Any],
    *,
    shuffle_partitions: int | None,
    advisory_partition_bytes: int,
    broadcast_max_bytes: int,
    target_file_bytes: int,
    skew_ratio_warn: float,
    skew_share_warn: float,
    cluster_min_observations: int,
    cluster_k_min: int,
    cluster_k_max: int,
) -> SparkRuntimePolicy:
    """Resolve explicit job overrides against source-size-derived policy defaults."""

    return SparkRuntimePolicy.from_manifest(
        manifest,
        shuffle_partitions=shuffle_partitions,
        advisory_partition_bytes=advisory_partition_bytes,
        broadcast_max_bytes=broadcast_max_bytes,
        target_file_bytes=target_file_bytes,
        skew_ratio_warn=skew_ratio_warn,
        skew_share_warn=skew_share_warn,
        cluster_min_observations=cluster_min_observations,
        cluster_k_min=cluster_k_min,
        cluster_k_max=cluster_k_max,
    )


def run_ingest_profile(
    *,
    source_batch_id: str,
    ingestion_id: str,
    benchmark_run_id: str,
    source_root: Path,
    bronze_root: Path,
    curated_root: Path,
    output_root: Path,
    evidence_path: Path,
    exact_distinct_max_rows: int,
    clustering_diagnostics_path: Path = Path(
        "reports/spark/clustering_diagnostics.json"
    ),
    shuffle_partitions: int | None = None,
    advisory_partition_bytes: int = DEFAULT_ADVISORY_PARTITION_BYTES,
    broadcast_max_bytes: int = DEFAULT_BROADCAST_MAX_BYTES,
    target_file_bytes: int = DEFAULT_TARGET_FILE_BYTES,
    skew_ratio_warn: float = DEFAULT_SKEW_RATIO_WARN,
    skew_share_warn: float = DEFAULT_SKEW_SHARE_WARN,
    cluster_min_observations: int = DEFAULT_CLUSTER_MIN_OBSERVATIONS,
    cluster_k_min: int = DEFAULT_CLUSTER_K_MIN,
    cluster_k_max: int = DEFAULT_CLUSTER_K_MAX,
) -> None:
    """Validate sources, publish Bronze/curated data, and produce run evidence.

    Stage ordering is kept here because it defines the transactional job
    contract: source validation precedes Spark startup, Bronze precedes the
    quality gate, and authoritative evidence is published only after every
    Spark action and post-run event-log summary succeeds.
    """

    started = time.perf_counter()
    source_directory = source_root / source_batch_id
    run_output = output_root / benchmark_run_id
    if run_output.exists():
        raise FileExistsError(f"Benchmark run already exists: {benchmark_run_id}")
    quality_path = run_output / "quality.json"

    source_manifest, source_checks = _source_validation_results(source_directory)
    headers = _header_results(source_directory)
    source_checks.extend(_source_readability_checks(headers))
    schema_checks = _schema_checks(headers)
    source_failed = any(not check["passed"] for check in source_checks)
    schema_failed = any(not check["passed"] for check in schema_checks)
    if source_failed or schema_failed:
        batch_sha256 = source_manifest.get("batch_sha256")
        quality = _pre_spark_failure_quality(
            headers=headers,
            source_batch_id=source_batch_id,
            source_batch_sha256=(
                batch_sha256 if isinstance(batch_sha256, str) else None
            ),
            ingestion_id=ingestion_id,
            checks=[*source_checks, *schema_checks],
        )
        _write_json(quality_path, quality)
        if source_failed:
            raise SourceValidationError("Source validation blocked Bronze publication.")
        raise QualityFailure("Schema drift blocked Bronze publication.")

    policy = _policy_from_manifest(
        source_manifest,
        shuffle_partitions=shuffle_partitions,
        advisory_partition_bytes=advisory_partition_bytes,
        broadcast_max_bytes=broadcast_max_bytes,
        target_file_bytes=target_file_bytes,
        skew_ratio_warn=skew_ratio_warn,
        skew_share_warn=skew_share_warn,
        cluster_min_observations=cluster_min_observations,
        cluster_k_min=cluster_k_min,
        cluster_k_max=cluster_k_max,
    )
    broadcast_decisions = policy.broadcast_decisions(source_manifest)

    bronze_target = bronze_root / f"ingestion_id={ingestion_id}"
    curated_target = curated_root / f"ingestion_id={ingestion_id}"
    if bronze_target.exists() or curated_target.exists():
        raise FileExistsError(f"Ingestion ID already exists: {ingestion_id}")

    event_log_directory = run_output / "eventlog"
    spark = create_spark_session(
        application_name=f"covid-bronze-{ingestion_id}",
        event_log_directory=event_log_directory,
        policy=policy,
    )
    frames: dict[str, DataFrame] = {}
    clustering: dict[str, Any]
    try:
        environment = _environment(spark, policy)
        ingested_at = datetime.now(UTC)
        frames = _read_sources(
            spark,
            source_directory,
            source_batch_id=source_batch_id,
            ingestion_id=ingestion_id,
            ingested_at=ingested_at,
        )
        profiles = _profiles(
            frames,
            source_directory,
            exact_distinct_max_rows=exact_distinct_max_rows,
        )
        daily = normalized_daily(
            frames["ecdc"],
            frames["mapping"],
            broadcast_mapping=broadcast_decisions["mapping"],
        )
        normalized_duplicates = duplicate_count(daily)
        assessment = evaluate_quality(
            profiles,
            headers,
            frames,
            normalized_duplicate_count=normalized_duplicates,
            cluster_min_observations=policy.cluster_min_observations,
        )
        assessment["checks"] = [*source_checks, *assessment["checks"]]
        quality = _quality_document(
            assessment=assessment,
            source_batch_id=source_batch_id,
            source_batch_sha256=source_manifest["batch_sha256"],
            ingestion_id=ingestion_id,
            headers=headers,
            profiles=profiles,
            publication={
                "bronze": True,
                "curated": assessment["status"] != "FAIL",
            },
        )
        quality_summary = _quality_summary(quality)
        _publish_bronze(
            frames,
            bronze_root=bronze_root,
            source_batch_id=source_batch_id,
            source_batch_sha256=source_manifest["batch_sha256"],
            source_files=source_manifest["files"],
            ingestion_id=ingestion_id,
            spark_application_id=environment["spark_application_id"],
            quality_summary=quality_summary,
            world_bank_snapshot_id=source_manifest.get("world_bank_snapshot_id"),
            snowflake_context_fingerprint=source_manifest.get(
                "snowflake_context_fingerprint"
            ),
            source_kind=source_manifest["source_kind"],
            runtime_policy=policy,
        )
        _write_json(quality_path, quality)
        if assessment["status"] == "FAIL":
            raise QualityFailure("Quality failures blocked curated publication.")

        suite = _benchmark_suite(
            spark,
            frames,
            benchmark_run_id=benchmark_run_id,
            run_output=run_output,
            snowflake_context_fingerprint=(
                source_manifest.get("snowflake_context_fingerprint")
                if source_manifest.get("source_kind") == "snowflake_export"
                else None
            ),
            policy=policy,
            broadcast_decisions=broadcast_decisions,
        )
        curated_metrics = _write_curated(
            suite.curated,
            curated_root=curated_root,
            ingestion_id=ingestion_id,
            layout=suite.layout,
        )
        suite.layout["published_output"] = curated_metrics
        clustering = _clustering_stage(
            spark,
            frames,
            policy=policy,
            broadcast_decisions=broadcast_decisions,
            model_id=benchmark_run_id,
            run_output=run_output,
            source_manifest=source_manifest,
            skew=suite.skew,
        )
        for name, evidence in suite.plans.items():
            (run_output / "plans").mkdir(parents=True, exist_ok=True)
            (run_output / "plans" / f"{name}.txt").write_text(
                evidence["normalized_plan"],
                encoding="utf-8",
            )
    finally:
        _release_spark_resources(spark, frames)

    event_metrics = parse_event_logs(event_log_directory)
    benchmarks = summarize_benchmarks(suite.benchmarks, event_metrics)
    clustering["pipeline_task_metrics"] = event_metrics.get("__all__", {})
    clustering["document_sha256"] = _document_sha256(clustering)
    cold_end_to_end_ms = round((time.perf_counter() - started) * 1000, 3)
    optimization = {
        "benchmark_run_id": benchmark_run_id,
        "cold_end_to_end_ms": cold_end_to_end_ms,
        "environment": environment,
        "layout": suite.layout,
        "plans": suite.plans,
        "benchmarks": benchmarks,
        "runtime_policy": policy.as_dict(),
        "broadcast_decisions": broadcast_decisions,
        "country_key_skew": suite.skew,
        "clustering": clustering,
    }
    _write_json(run_output / "optimization_metrics.json", optimization)
    evidence = _sanitized_evidence(
        environment=environment,
        source_manifest=source_manifest,
        ingestion_id=ingestion_id,
        benchmark_run_id=benchmark_run_id,
        benchmarks=benchmarks,
        layout=suite.layout,
        plans=suite.plans,
        quality=quality_summary,
        cold_end_to_end_ms=cold_end_to_end_ms,
        context_equivalence=suite.context_equivalence,
        runtime_policy=policy,
        broadcast_decisions=broadcast_decisions,
        skew=suite.skew,
        clustering=clustering,
        pipeline_task_metrics=event_metrics.get("__all__", {}),
    )
    _publish_evidence_documents(
        source_kind=source_manifest.get("source_kind"),
        run_output=run_output,
        evidence_path=evidence_path,
        clustering_diagnostics_path=clustering_diagnostics_path,
        evidence=evidence,
        clustering=clustering,
    )
    logger.info(
        "spark_ingest_profile_completed",
        extra={
            "source_batch_id": source_batch_id,
            "ingestion_id": ingestion_id,
            "benchmark_run_id": benchmark_run_id,
            "quality_status": quality["status"],
        },
    )


def run_benchmark(
    *,
    ingestion_id: str,
    benchmark_run_id: str,
    bronze_root: Path,
    output_root: Path,
    evidence_path: Path,
    clustering_diagnostics_path: Path = Path(
        "reports/spark/clustering_diagnostics.json"
    ),
    shuffle_partitions: int | None = None,
    advisory_partition_bytes: int = DEFAULT_ADVISORY_PARTITION_BYTES,
    broadcast_max_bytes: int = DEFAULT_BROADCAST_MAX_BYTES,
    target_file_bytes: int = DEFAULT_TARGET_FILE_BYTES,
    skew_ratio_warn: float = DEFAULT_SKEW_RATIO_WARN,
    skew_share_warn: float = DEFAULT_SKEW_SHARE_WARN,
    cluster_min_observations: int = DEFAULT_CLUSTER_MIN_OBSERVATIONS,
    cluster_k_min: int = DEFAULT_CLUSTER_K_MIN,
    cluster_k_max: int = DEFAULT_CLUSTER_K_MAX,
) -> None:
    """Benchmark an existing quality-approved Bronze ingestion.

    Benchmark-only execution deliberately reuses the same orchestration and
    evidence modules as ingest mode, preventing schema or report drift while
    avoiding any source reread or Bronze rewrite.
    """

    started = time.perf_counter()
    ingestion_directory = bronze_root / f"ingestion_id={ingestion_id}"
    bronze_manifest = json.loads(
        (ingestion_directory / "manifest.json").read_text(encoding="utf-8")
    )
    quality = _quality_from_bronze_manifest(bronze_manifest)
    policy = _policy_from_manifest(
        bronze_manifest,
        shuffle_partitions=shuffle_partitions,
        advisory_partition_bytes=advisory_partition_bytes,
        broadcast_max_bytes=broadcast_max_bytes,
        target_file_bytes=target_file_bytes,
        skew_ratio_warn=skew_ratio_warn,
        skew_share_warn=skew_share_warn,
        cluster_min_observations=cluster_min_observations,
        cluster_k_min=cluster_k_min,
        cluster_k_max=cluster_k_max,
    )
    broadcast_decisions = policy.broadcast_decisions(bronze_manifest)
    run_output = output_root / benchmark_run_id
    if run_output.exists():
        raise FileExistsError(f"Benchmark run already exists: {benchmark_run_id}")
    event_log_directory = run_output / "eventlog"
    spark = create_spark_session(
        application_name=f"covid-benchmark-{benchmark_run_id}",
        event_log_directory=event_log_directory,
        policy=policy,
    )
    frames: dict[str, DataFrame] = {}
    clustering: dict[str, Any]
    try:
        environment = _environment(spark, policy)
        frames = _read_bronze(spark, ingestion_directory)
        suite = _benchmark_suite(
            spark,
            frames,
            benchmark_run_id=benchmark_run_id,
            run_output=run_output,
            snowflake_context_fingerprint=(
                bronze_manifest.get("snowflake_context_fingerprint")
                if bronze_manifest.get("source_kind") == "snowflake_export"
                else None
            ),
            policy=policy,
            broadcast_decisions=broadcast_decisions,
        )
        source_manifest = {
            "source_batch_id": bronze_manifest["source_batch_id"],
            "source_batch_sha256": bronze_manifest["source_batch_sha256"],
            "source_files": bronze_manifest["source_files"],
            "source_kind": bronze_manifest.get("source_kind"),
            "world_bank_snapshot_id": bronze_manifest.get("world_bank_snapshot_id"),
        }
        clustering = _clustering_stage(
            spark,
            frames,
            policy=policy,
            broadcast_decisions=broadcast_decisions,
            model_id=benchmark_run_id,
            run_output=run_output,
            source_manifest=source_manifest,
            skew=suite.skew,
        )
    finally:
        _release_spark_resources(spark, frames)
    event_metrics = parse_event_logs(event_log_directory)
    benchmarks = summarize_benchmarks(suite.benchmarks, event_metrics)
    clustering["pipeline_task_metrics"] = event_metrics.get("__all__", {})
    clustering["document_sha256"] = _document_sha256(clustering)
    source_manifest = {
        "source_batch_id": bronze_manifest["source_batch_id"],
        "batch_sha256": bronze_manifest["source_batch_sha256"],
        "files": bronze_manifest["source_files"],
        "world_bank_snapshot_id": bronze_manifest.get("world_bank_snapshot_id"),
        "source_kind": bronze_manifest.get("source_kind"),
    }
    cold_end_to_end_ms = round((time.perf_counter() - started) * 1000, 3)
    evidence = _sanitized_evidence(
        environment=environment,
        source_manifest=source_manifest,
        ingestion_id=ingestion_id,
        benchmark_run_id=benchmark_run_id,
        benchmarks=benchmarks,
        layout=suite.layout,
        plans=suite.plans,
        quality=quality,
        cold_end_to_end_ms=cold_end_to_end_ms,
        context_equivalence=suite.context_equivalence,
        runtime_policy=policy,
        broadcast_decisions=broadcast_decisions,
        skew=suite.skew,
        clustering=clustering,
        pipeline_task_metrics=event_metrics.get("__all__", {}),
    )
    _write_json(run_output / "optimization_metrics.json", evidence)
    _publish_evidence_documents(
        source_kind=bronze_manifest.get("source_kind"),
        run_output=run_output,
        evidence_path=evidence_path,
        clustering_diagnostics_path=clustering_diagnostics_path,
        evidence=evidence,
        clustering=clustering,
    )
