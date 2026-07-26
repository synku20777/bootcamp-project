from __future__ import annotations

import hashlib
import json
import logging
import platform
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pyspark import StorageLevel
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from app.spark_pipeline.benchmark import (
    BenchmarkRunner,
    directory_metrics,
    layout_decision,
    parse_event_logs,
    plan_evidence,
    summarize_benchmarks,
)
from app.spark_pipeline.quality import (
    QUALITY_RULESET_VERSION,
    evaluate_quality,
    inspect_header,
    profile_dataset,
    read_bronze_source,
)
from app.spark_pipeline.schemas import CORRUPT_RECORD_COLUMN, DATASETS
from app.spark_pipeline.transformations import (
    duplicate_count,
    enrich_with_population,
    normalized_daily,
)

logger = logging.getLogger(__name__)


class SparkPipelineError(RuntimeError):
    """A controlled Spark pipeline failure."""


class QualityFailure(SparkPipelineError):
    """Quality rules blocked downstream publication."""


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_and_verify_source_manifest(source_directory: Path) -> dict[str, Any]:
    manifest_path = source_directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for details in manifest["files"].values():
        source_path = source_directory / details["filename"]
        if not source_path.is_file() or _file_sha256(source_path) != details["sha256"]:
            raise SparkPipelineError("Source batch checksum verification failed.")
    return manifest


def create_spark_session(
    *,
    application_name: str,
    event_log_directory: Path,
) -> SparkSession:
    event_log_directory.mkdir(parents=True, exist_ok=True)
    spark = (
        SparkSession.builder.master("local[2]")
        .appName(application_name)
        .config("spark.ui.enabled", "false")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
        .config("spark.sql.adaptive.advisoryPartitionSizeInBytes", 1024 * 1024)
        .config("spark.sql.shuffle.partitions", 32)
        .config("spark.sql.autoBroadcastJoinThreshold", -1)
        .config("spark.eventLog.enabled", "true")
        .config("spark.eventLog.dir", event_log_directory.resolve().as_uri())
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    return spark


def _header_results(source_directory: Path) -> dict[str, dict[str, Any]]:
    return {
        dataset.name: inspect_header(
            source_directory / dataset.filename,
            dataset.headers,
        )
        for dataset in DATASETS
    }


def _schema_failure_quality(
    *,
    headers: dict[str, dict[str, Any]],
    source_batch_id: str,
    ingestion_id: str,
) -> dict[str, Any]:
    checks = [
        {
            "rule": f"{name}.schema_exact",
            "severity": "FAIL",
            "count": 0 if result["matches"] else 1,
            "passed": result["matches"],
            "message": "CSV header must exactly match the versioned contract.",
        }
        for name, result in headers.items()
    ]
    return {
        "ruleset_version": QUALITY_RULESET_VERSION,
        "status": "FAIL",
        "source_batch_id": source_batch_id,
        "ingestion_id": ingestion_id,
        "headers": headers,
        "profiles": {},
        "checks": checks,
        "publication": {"bronze": False, "curated": False},
    }


def _read_sources(
    spark: SparkSession,
    source_directory: Path,
    *,
    source_batch_id: str,
    ingestion_id: str,
    ingested_at: datetime,
) -> dict[str, DataFrame]:
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
    return {
        dataset.name: profile_dataset(
            frames[dataset.name],
            dataset,
            input_bytes=(source_directory / dataset.filename).stat().st_size,
            exact_distinct_max_rows=exact_distinct_max_rows,
        )
        for dataset in DATASETS
    }


def _publish_bronze(
    frames: dict[str, DataFrame],
    *,
    bronze_root: Path,
    source_batch_id: str,
    source_batch_sha256: str,
    source_files: dict[str, Any],
    ingestion_id: str,
    spark_application_id: str,
) -> Path:
    target = bronze_root / f"ingestion_id={ingestion_id}"
    if target.exists():
        raise FileExistsError(f"Bronze ingestion already exists: {ingestion_id}")
    staging = bronze_root / f".staging-{ingestion_id}-{uuid4().hex}"
    staging.mkdir(parents=True)
    try:
        for name, frame in frames.items():
            frame.coalesce(1).write.mode("error").parquet(
                str(staging / name / "records")
            )
            frame.where(F.col(CORRUPT_RECORD_COLUMN).isNotNull()).coalesce(
                1
            ).write.mode("error").parquet(str(staging / name / "corrupt_records"))
        _write_json(
            staging / "manifest.json",
            {
                "manifest_version": 1,
                "source_batch_id": source_batch_id,
                "source_batch_sha256": source_batch_sha256,
                "source_files": source_files,
                "ingestion_id": ingestion_id,
                "spark_application_id": spark_application_id,
                "datasets": list(frames),
            },
        )
        staging.replace(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def _read_bronze(
    spark: SparkSession, ingestion_directory: Path
) -> dict[str, DataFrame]:
    return {
        dataset.name: spark.read.parquet(
            str(ingestion_directory / dataset.name / "records")
        ).persist(StorageLevel.MEMORY_AND_DISK)
        for dataset in DATASETS
    }


def _count_action(dataframe: DataFrame) -> Any:
    def action() -> dict[str, Any]:
        return {
            "row_count": dataframe.count(),
            "partitions": dataframe.rdd.getNumPartitions(),
        }

    return action


def _projection_actions(ecdc: DataFrame) -> tuple[Any, Any]:
    full = ecdc
    projected = ecdc.select(
        "COUNTRY_REGION",
        "ISO3166_1",
        "REPORT_DATE",
        "CASES",
        "DEATHS",
    ).where(F.col("COUNTRY_REGION").isNotNull() & F.col("REPORT_DATE").isNotNull())

    def aggregate(dataframe: DataFrame) -> dict[str, Any]:
        row = dataframe.agg(
            F.count(F.lit(1)).alias("rows"),
            F.sum(F.coalesce("CASES", F.lit(0))).alias("cases"),
            F.sum(F.coalesce("DEATHS", F.lit(0))).alias("deaths"),
        ).first()
        return {
            "row_count": int(row["rows"]),
            "column_count": len(dataframe.columns),
            "partitions": dataframe.rdd.getNumPartitions(),
        }

    return lambda: aggregate(full), lambda: aggregate(projected)


def _aqe_action(ecdc: DataFrame, spark: SparkSession, *, enabled: bool) -> Any:
    def action() -> dict[str, Any]:
        spark.conf.set("spark.sql.adaptive.enabled", str(enabled).lower())
        aggregation = (
            ecdc.select("COUNTRY_REGION", "REPORT_DATE", "CASES", "DEATHS")
            .groupBy("COUNTRY_REGION", "REPORT_DATE")
            .agg(F.sum("CASES"), F.sum("DEATHS"))
        )
        row_count = aggregation.count()
        return {
            "row_count": row_count,
            "partitions": aggregation.rdd.getNumPartitions(),
        }

    return action


def _cache_action(ecdc: DataFrame, *, cache_enabled: bool) -> Any:
    def action() -> dict[str, Any]:
        reused = ecdc.select("COUNTRY_REGION", "REPORT_DATE", "CASES", "DEATHS")
        if cache_enabled:
            reused = reused.persist(StorageLevel.MEMORY_AND_DISK)
            reused.count()
        reused.agg(F.sum("CASES"), F.sum("DEATHS")).collect()
        reused.groupBy("COUNTRY_REGION").count().collect()
        partitions = reused.rdd.getNumPartitions()
        if cache_enabled:
            reused.unpersist()
        return {"partitions": partitions, "reuse_count": 2}

    return action


def _write_action(
    dataframe: DataFrame,
    root: Path,
    *,
    partitions: int,
    repartition: bool,
) -> Any:
    def action() -> dict[str, Any]:
        target = root / uuid4().hex
        output = (
            dataframe.repartition(partitions)
            if repartition
            else dataframe.coalesce(partitions)
        )
        output.write.mode("error").parquet(str(target))
        metrics = directory_metrics(target)
        shutil.rmtree(target)
        return {**metrics, "partitions": partitions}

    return action


def _calibrate_layout(dataframe: DataFrame, root: Path) -> dict[str, Any]:
    target = root / "calibration"
    dataframe.repartition(8).write.mode("error").parquet(str(target))
    metrics = directory_metrics(target)
    total_rows = dataframe.count()
    month_rows = [
        row["count"]
        for row in dataframe.groupBy(
            F.year("report_date").alias("year"),
            F.month("report_date").alias("month"),
        )
        .count()
        .collect()
    ]
    monthly_bytes = [
        round(metrics["output_bytes"] * count / total_rows) if total_rows else 0
        for count in month_rows
    ]
    decision = layout_decision(
        measured_parquet_bytes=metrics["output_bytes"],
        monthly_parquet_bytes=monthly_bytes,
    )
    shutil.rmtree(target)
    return decision


def _benchmark_suite(
    spark: SparkSession,
    frames: dict[str, DataFrame],
    *,
    benchmark_run_id: str,
    scratch_root: Path,
) -> tuple[list[dict[str, Any]], DataFrame, dict[str, Any], dict[str, Any]]:
    runner = BenchmarkRunner(spark, benchmark_run_id)
    ecdc = frames["ecdc"]
    mapping = frames["mapping"]
    population = frames["population"]
    projection_before, projection_after = _projection_actions(ecdc)

    baseline_daily = normalized_daily(ecdc, mapping, broadcast_mapping=False)
    optimized_daily = normalized_daily(ecdc, mapping, broadcast_mapping=True)
    baseline_enriched = enrich_with_population(
        baseline_daily,
        population,
        broadcast_population=False,
    )
    optimized_enriched = enrich_with_population(
        optimized_daily,
        population,
        broadcast_population=True,
    )

    benchmarks = [
        runner.compare(
            name="early_projection_filter",
            before=projection_before,
            after=projection_after,
        ),
        runner.compare(
            name="broadcast_joins",
            before=_count_action(baseline_enriched),
            after=_count_action(optimized_enriched),
        ),
        runner.compare(
            name="adaptive_duplicate_aggregation",
            before=_aqe_action(ecdc, spark, enabled=False),
            after=_aqe_action(ecdc, spark, enabled=True),
        ),
        runner.compare(
            name="reused_frame_cache",
            before=_cache_action(ecdc, cache_enabled=False),
            after=_cache_action(ecdc, cache_enabled=True),
        ),
    ]
    spark.conf.set("spark.sql.adaptive.enabled", "true")
    optimized_enriched.count()
    baseline_enriched.count()
    optimized_plan = plan_evidence(optimized_enriched)
    baseline_plan = plan_evidence(baseline_enriched)
    if optimized_plan["broadcast_hash_join_count"] < 2:
        raise SparkPipelineError("Optimized plan did not use both broadcast joins.")
    if optimized_plan["build_right_count"] < 2:
        raise SparkPipelineError("Optimized joins did not build both right dimensions.")
    if not optimized_plan["null_safe_mapping_key_present"]:
        raise SparkPipelineError("Mapping plan does not show null-safe key matching.")
    if not optimized_plan["typed_population_key_present"]:
        raise SparkPipelineError("Population plan does not show the typed lookup key.")

    scratch_root.mkdir(parents=True, exist_ok=True)
    layout = _calibrate_layout(optimized_enriched, scratch_root)
    benchmarks.append(
        runner.compare(
            name="file_layout",
            before=_write_action(
                optimized_enriched,
                scratch_root,
                partitions=8,
                repartition=True,
            ),
            after=_write_action(
                optimized_enriched,
                scratch_root,
                partitions=layout["target_file_count"],
                repartition=False,
            ),
        )
    )
    return (
        benchmarks,
        optimized_enriched,
        layout,
        {
            "baseline": baseline_plan,
            "optimized": optimized_plan,
        },
    )


def _write_curated(
    dataframe: DataFrame,
    *,
    curated_root: Path,
    ingestion_id: str,
    layout: dict[str, Any],
) -> dict[str, int]:
    target = curated_root / f"ingestion_id={ingestion_id}"
    if target.exists():
        raise FileExistsError(f"Curated ingestion already exists: {ingestion_id}")
    staging = curated_root / f".staging-{ingestion_id}-{uuid4().hex}"
    output = dataframe
    writer = output.coalesce(layout["target_file_count"]).write.mode("error")
    try:
        if layout["partition_columns"]:
            output = output.withColumn("report_year", F.year("report_date")).withColumn(
                "report_month", F.month("report_date")
            )
            writer = (
                output.repartition(
                    layout["target_file_count"],
                    "report_year",
                    "report_month",
                )
                .write.mode("error")
                .partitionBy("report_year", "report_month")
            )
        writer.parquet(str(staging))
        metrics = directory_metrics(staging)
        staging.replace(target)
        return metrics
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _environment(spark: SparkSession) -> dict[str, Any]:
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
    }


def _conclusions(benchmarks: list[dict[str, Any]]) -> list[str]:
    conclusions = []
    for benchmark in benchmarks:
        before = benchmark["summary"]["before"]["duration_ms"]["median"]
        after = benchmark["summary"]["after"]["duration_ms"]["median"]
        direction = "faster" if after < before else "not faster"
        conclusions.append(
            f"{benchmark['name']}: optimized median was {direction} "
            f"({after} ms versus {before} ms)."
        )
    return conclusions


def _sanitized_evidence(
    *,
    environment: dict[str, Any],
    source_manifest: dict[str, Any],
    ingestion_id: str,
    benchmark_run_id: str,
    benchmarks: list[dict[str, Any]],
    layout: dict[str, Any],
    plans: dict[str, Any],
    quality: dict[str, Any],
    cold_end_to_end_ms: float,
) -> dict[str, Any]:
    compact_plans = {
        name: {key: value for key, value in plan.items() if key != "normalized_plan"}
        for name, plan in plans.items()
    }
    return {
        "evidence_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "environment": environment,
        "source_batch": {
            "source_batch_id": source_manifest["source_batch_id"],
            "batch_sha256": source_manifest["batch_sha256"],
            "files": {
                name: {
                    "row_count": details["row_count"],
                    "byte_count": details["byte_count"],
                    "sha256": details["sha256"],
                }
                for name, details in source_manifest["files"].items()
            },
        },
        "ingestion_id": ingestion_id,
        "benchmark_run_id": benchmark_run_id,
        "cold_end_to_end_ms": cold_end_to_end_ms,
        "quality": {
            "ruleset_version": quality["ruleset_version"],
            "status": quality["status"],
        },
        "layout": layout,
        "plans": compact_plans,
        "benchmarks": benchmarks,
        "conclusions": _conclusions(benchmarks),
        "limitations": [
            "The workload is too small for Spark throughput benefits.",
            "Measurements are local-mode results and do not represent a cluster.",
            "Warm runs reduce startup and JIT noise but cannot remove filesystem-cache effects.",
            "Snowflake SQL remains the production semantic layer.",
        ],
    }


def _quality_for_ingestion(output_root: Path, ingestion_id: str) -> dict[str, str]:
    for path in sorted(output_root.glob("*/quality.json"), reverse=True):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("ingestion_id") == ingestion_id:
            return {
                "ruleset_version": payload["ruleset_version"],
                "status": payload["status"],
            }
    raise SparkPipelineError("No quality result exists for the Bronze ingestion.")


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
) -> None:
    started = time.perf_counter()
    source_directory = source_root / source_batch_id
    source_manifest = load_and_verify_source_manifest(source_directory)
    headers = _header_results(source_directory)
    run_output = output_root / benchmark_run_id
    if run_output.exists():
        raise FileExistsError(f"Benchmark run already exists: {benchmark_run_id}")
    quality_path = run_output / "quality.json"
    if not all(result["matches"] for result in headers.values()):
        quality = _schema_failure_quality(
            headers=headers,
            source_batch_id=source_batch_id,
            source_batch_sha256=source_manifest["batch_sha256"],
            ingestion_id=ingestion_id,
        )
        _write_json(quality_path, quality)
        raise QualityFailure("Schema drift blocked Bronze publication.")

    bronze_target = bronze_root / f"ingestion_id={ingestion_id}"
    curated_target = curated_root / f"ingestion_id={ingestion_id}"
    if bronze_target.exists() or curated_target.exists():
        raise FileExistsError(f"Ingestion ID already exists: {ingestion_id}")

    event_log_directory = run_output / "eventlog"
    spark = create_spark_session(
        application_name=f"covid-bronze-{ingestion_id}",
        event_log_directory=event_log_directory,
    )
    environment = _environment(spark)
    frames: dict[str, DataFrame] = {}
    try:
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
            frames["ecdc"], frames["mapping"], broadcast_mapping=True
        )
        normalized_duplicates = duplicate_count(daily)
        assessment = evaluate_quality(
            profiles,
            headers,
            frames,
            normalized_duplicate_count=normalized_duplicates,
        )
        quality = {
            **assessment,
            "source_batch_id": source_batch_id,
            "ingestion_id": ingestion_id,
            "headers": headers,
            "profiles": profiles,
            "publication": {"bronze": True, "curated": assessment["status"] != "FAIL"},
        }
        _publish_bronze(
            frames,
            bronze_root=bronze_root,
            source_batch_id=source_batch_id,
            source_batch_sha256=source_manifest["batch_sha256"],
            source_files=source_manifest["files"],
            ingestion_id=ingestion_id,
            spark_application_id=environment["spark_application_id"],
        )
        _write_json(quality_path, quality)
        if assessment["status"] == "FAIL":
            raise QualityFailure("Quality failures blocked curated publication.")

        benchmarks, curated, layout, plans = _benchmark_suite(
            spark,
            frames,
            benchmark_run_id=benchmark_run_id,
            scratch_root=run_output / "benchmark_scratch",
        )
        curated_metrics = _write_curated(
            curated,
            curated_root=curated_root,
            ingestion_id=ingestion_id,
            layout=layout,
        )
        layout["published_output"] = curated_metrics
        for name, evidence in plans.items():
            (run_output / "plans").mkdir(parents=True, exist_ok=True)
            (run_output / "plans" / f"{name}.txt").write_text(
                evidence["normalized_plan"],
                encoding="utf-8",
            )
    finally:
        for frame in frames.values():
            frame.unpersist()
        spark.stop()

    event_metrics = parse_event_logs(event_log_directory)
    benchmarks = summarize_benchmarks(benchmarks, event_metrics)
    cold_end_to_end_ms = round((time.perf_counter() - started) * 1000, 3)
    optimization = {
        "benchmark_run_id": benchmark_run_id,
        "cold_end_to_end_ms": cold_end_to_end_ms,
        "environment": environment,
        "layout": layout,
        "plans": plans,
        "benchmarks": benchmarks,
    }
    _write_json(run_output / "optimization_metrics.json", optimization)
    evidence = _sanitized_evidence(
        environment=environment,
        source_manifest=source_manifest,
        ingestion_id=ingestion_id,
        benchmark_run_id=benchmark_run_id,
        benchmarks=benchmarks,
        layout=layout,
        plans=plans,
        quality=quality,
        cold_end_to_end_ms=cold_end_to_end_ms,
    )
    _write_json(evidence_path, evidence)
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
) -> None:
    started = time.perf_counter()
    ingestion_directory = bronze_root / f"ingestion_id={ingestion_id}"
    bronze_manifest = json.loads(
        (ingestion_directory / "manifest.json").read_text(encoding="utf-8")
    )
    run_output = output_root / benchmark_run_id
    if run_output.exists():
        raise FileExistsError(f"Benchmark run already exists: {benchmark_run_id}")
    event_log_directory = run_output / "eventlog"
    spark = create_spark_session(
        application_name=f"covid-benchmark-{benchmark_run_id}",
        event_log_directory=event_log_directory,
    )
    environment = _environment(spark)
    frames = _read_bronze(spark, ingestion_directory)
    try:
        benchmarks, _curated, layout, plans = _benchmark_suite(
            spark,
            frames,
            benchmark_run_id=benchmark_run_id,
            scratch_root=run_output / "benchmark_scratch",
        )
    finally:
        for frame in frames.values():
            frame.unpersist()
        spark.stop()
    benchmarks = summarize_benchmarks(
        benchmarks,
        parse_event_logs(event_log_directory),
    )
    source_manifest = {
        "source_batch_id": bronze_manifest["source_batch_id"],
        "batch_sha256": bronze_manifest["source_batch_sha256"],
        "files": bronze_manifest["source_files"],
    }
    quality = _quality_for_ingestion(output_root, ingestion_id)
    cold_end_to_end_ms = round((time.perf_counter() - started) * 1000, 3)
    evidence = _sanitized_evidence(
        environment=environment,
        source_manifest=source_manifest,
        ingestion_id=ingestion_id,
        benchmark_run_id=benchmark_run_id,
        benchmarks=benchmarks,
        layout=layout,
        plans=plans,
        quality=quality,
        cold_end_to_end_ms=cold_end_to_end_ms,
    )
    _write_json(run_output / "optimization_metrics.json", evidence)
    _write_json(evidence_path, evidence)
