from __future__ import annotations

import hashlib
import json
import logging
import platform
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pyspark import StorageLevel
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from app.country_context_fingerprint import country_context_fingerprint
from app.spark_pipeline.benchmark import (
    FINGERPRINT_PROTOCOL,
    BenchmarkRunner,
    CorrectnessOutputs,
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
    country_baseline,
    duplicate_count,
    enrich_with_country_context,
    enrich_with_population,
    normalized_daily,
)

logger = logging.getLogger(__name__)


class SparkPipelineError(RuntimeError):
    """A controlled Spark pipeline failure."""


class QualityFailure(SparkPipelineError):
    """Quality rules blocked downstream publication."""


class SourceValidationError(QualityFailure):
    """Source files or their manifest failed the pre-Spark contract."""


@dataclass(frozen=True, slots=True)
class BenchmarkSuiteResult:
    benchmarks: list[dict[str, Any]]
    curated: DataFrame
    layout: dict[str, Any]
    plans: dict[str, Any]
    context_equivalence: dict[str, Any]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_document(payload), encoding="utf-8")


def _json_document(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def _document_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_json_document(payload).encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{path.name}.staging-{uuid4().hex}"
    try:
        staging.write_text(_json_document(payload), encoding="utf-8")
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def _file_sha256(path: Path) -> str:
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
        manifest.get("manifest_version") == 2
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
                "The source manifest must use version 2 and include batch, file, "
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


def _header_results(source_directory: Path) -> dict[str, dict[str, Any]]:
    return {
        dataset.name: inspect_header(
            source_directory / dataset.filename,
            dataset.headers,
        )
        for dataset in DATASETS
    }


def _schema_checks(headers: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
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
    quality_summary: dict[str, str],
    world_bank_snapshot_id: str | None,
    snowflake_context_fingerprint: dict[str, Any] | None,
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
                "manifest_version": 2,
                "source_batch_id": source_batch_id,
                "source_batch_sha256": source_batch_sha256,
                "source_files": source_files,
                "ingestion_id": ingestion_id,
                "spark_application_id": spark_application_id,
                "datasets": list(frames),
                "quality_summary": quality_summary,
                "world_bank_snapshot_id": world_bank_snapshot_id,
                "snowflake_context_fingerprint": snowflake_context_fingerprint,
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


def _single_output(name: str, dataframe: DataFrame) -> CorrectnessOutputs:
    return CorrectnessOutputs({name: dataframe})


def _country_context_equivalence(
    enriched: DataFrame,
    expected: dict[str, Any] | None,
) -> dict[str, Any]:
    projection = (
        enriched.where(F.col("context_iso3").isNotNull())
        .select(
            F.col("context_iso3").alias("ISO3"),
            F.col("population_2020_context").alias("POPULATION_2020_CONTEXT"),
            F.col("population_density_2019").alias("POPULATION_DENSITY_2019"),
            F.col("population_age_65_plus_pct_2019").alias(
                "POPULATION_AGE_65_PLUS_PCT_2019"
            ),
            F.col("real_gdp_per_capita_2019").alias("REAL_GDP_PER_CAPITA_2019"),
            F.col("health_expenditure_per_capita_ppp_2019").alias(
                "HEALTH_EXPENDITURE_PER_CAPITA_PPP_2019"
            ),
            F.col("context_snapshot_id").alias("SNAPSHOT_ID"),
        )
        .dropDuplicates(["ISO3"])
    )
    actual = country_context_fingerprint(
        row.asDict(recursive=True) for row in projection.toLocalIterator()
    )
    matches = expected is None or actual == expected
    if not matches:
        raise SparkPipelineError(
            "Spark country context does not match the Snowflake baseline fingerprint."
        )
    snapshot_ids = sorted(
        str(row["SNAPSHOT_ID"])
        for row in projection.select("SNAPSHOT_ID").distinct().collect()
    )
    return {
        "snowflake": expected,
        "spark": actual,
        "fingerprints_match": matches,
        "snapshot_ids": snapshot_ids,
        "baseline_rows": actual["row_count"],
        "baseline_projection_canonical_bytes": actual["canonical_bytes"],
        "joined_covid_rows": enriched.count(),
        "unmatched_location_count": enriched.where(
            F.col("context_snapshot_id").isNull()
        )
        .select("location_key")
        .distinct()
        .count(),
    }


def _projected_input(ecdc: DataFrame) -> DataFrame:
    return ecdc.select(
        "COUNTRY_REGION",
        "ISO3166_1",
        "REPORT_DATE",
        "CASES",
        "DEATHS",
    ).where(F.col("COUNTRY_REGION").isNotNull() & F.col("REPORT_DATE").isNotNull())


def _projection_result(dataframe: DataFrame) -> DataFrame:
    return dataframe.agg(
        F.count(F.lit(1)).alias("rows"),
        F.sum(F.coalesce("CASES", F.lit(0))).alias("cases"),
        F.sum(F.coalesce("DEATHS", F.lit(0))).alias("deaths"),
    )


def _projection_action(dataframe: DataFrame) -> Any:
    def action() -> dict[str, Any]:
        row = _projection_result(dataframe).first()
        return {
            "row_count": int(row["rows"]),
            "column_count": len(dataframe.columns),
            "partitions": dataframe.rdd.getNumPartitions(),
        }

    return action


def _duplicate_aggregation(ecdc: DataFrame) -> DataFrame:
    return (
        ecdc.select("COUNTRY_REGION", "REPORT_DATE", "CASES", "DEATHS")
        .groupBy("COUNTRY_REGION", "REPORT_DATE")
        .agg(
            F.sum("CASES").alias("cases"),
            F.sum("DEATHS").alias("deaths"),
        )
    )


def _aqe_action(ecdc: DataFrame) -> Any:
    def action() -> dict[str, Any]:
        aggregation = _duplicate_aggregation(ecdc)
        row_count = aggregation.count()
        return {
            "row_count": row_count,
            "partitions": aggregation.rdd.getNumPartitions(),
        }

    return action


def _cache_input(ecdc: DataFrame) -> DataFrame:
    return ecdc.select("COUNTRY_REGION", "REPORT_DATE", "CASES", "DEATHS")


def _cache_results(reused: DataFrame) -> dict[str, DataFrame]:
    return {
        "totals": reused.agg(
            F.sum("CASES").alias("cases"),
            F.sum("DEATHS").alias("deaths"),
        ),
        "country_counts": reused.groupBy("COUNTRY_REGION")
        .count()
        .select(
            F.col("COUNTRY_REGION").alias("country"),
            F.col("count").cast("long").alias("row_count"),
        ),
    }


def _cache_action(ecdc: DataFrame, *, cache_enabled: bool) -> Any:
    def action() -> dict[str, Any]:
        reused = _cache_input(ecdc)
        if cache_enabled:
            reused = reused.persist(StorageLevel.MEMORY_AND_DISK)
            reused.count()
        try:
            for dataframe in _cache_results(reused).values():
                dataframe.collect()
            return {
                "partitions": reused.rdd.getNumPartitions(),
                "reuse_count": 2,
            }
        finally:
            if cache_enabled:
                reused.unpersist()

    return action


def _cache_correctness(ecdc: DataFrame, *, cache_enabled: bool) -> CorrectnessOutputs:
    reused = _cache_input(ecdc)
    if cache_enabled:
        reused = reused.persist(StorageLevel.MEMORY_AND_DISK)
        reused.count()
    cleanup = reused.unpersist if cache_enabled else (lambda: None)
    return CorrectnessOutputs(_cache_results(reused), cleanup)


def _write_dataframe(
    dataframe: DataFrame,
    target: Path,
    *,
    partitions: int,
    repartition: bool,
) -> dict[str, int]:
    output = (
        dataframe.repartition(partitions)
        if repartition
        else dataframe.coalesce(partitions)
    )
    output.write.mode("error").parquet(str(target))
    return directory_metrics(target)


def _write_action(
    dataframe: DataFrame,
    root: Path,
    *,
    partitions: int,
    repartition: bool,
) -> Any:
    def action() -> dict[str, Any]:
        target = root / uuid4().hex
        try:
            metrics = _write_dataframe(
                dataframe,
                target,
                partitions=partitions,
                repartition=repartition,
            )
            return {**metrics, "partitions": partitions}
        finally:
            shutil.rmtree(target, ignore_errors=True)

    return action


def _write_correctness(
    spark: SparkSession,
    dataframe: DataFrame,
    root: Path,
    *,
    partitions: int,
    repartition: bool,
) -> CorrectnessOutputs:
    target = root / f"correctness-{uuid4().hex}"
    _write_dataframe(
        dataframe,
        target,
        partitions=partitions,
        repartition=repartition,
    )
    read_back = spark.read.parquet(str(target))
    return CorrectnessOutputs(
        {"parquet_read_back": read_back},
        lambda: shutil.rmtree(target, ignore_errors=True),
    )


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
    run_output: Path,
    snowflake_context_fingerprint: dict[str, Any] | None,
) -> BenchmarkSuiteResult:
    scratch_root = run_output / "benchmark_scratch"
    runner = BenchmarkRunner(
        spark,
        benchmark_run_id,
        run_output / "correctness_failure.json",
    )
    ecdc = frames["ecdc"]
    mapping = frames["mapping"]
    population = frames["population"]
    baseline = country_baseline(frames["indicators"])
    projected_ecdc = _projected_input(ecdc)

    baseline_daily = normalized_daily(ecdc, mapping, broadcast_mapping=False)
    optimized_daily = normalized_daily(ecdc, mapping, broadcast_mapping=True)
    baseline_enriched = enrich_with_country_context(
        enrich_with_population(
            baseline_daily,
            population,
            broadcast_population=False,
        ),
        baseline,
        broadcast_baseline=False,
    )
    optimized_enriched = enrich_with_country_context(
        enrich_with_population(
            optimized_daily,
            population,
            broadcast_population=True,
        ),
        baseline,
        broadcast_baseline=True,
    )

    benchmarks = [
        runner.compare(
            name="early_projection_filter",
            before=_projection_action(ecdc),
            after=_projection_action(projected_ecdc),
            correctness_before=lambda: _single_output(
                "aggregate", _projection_result(ecdc)
            ),
            correctness_after=lambda: _single_output(
                "aggregate", _projection_result(projected_ecdc)
            ),
        ),
        runner.compare(
            name="broadcast_joins",
            before=_count_action(baseline_enriched),
            after=_count_action(optimized_enriched),
            correctness_before=lambda: _single_output("enriched", baseline_enriched),
            correctness_after=lambda: _single_output("enriched", optimized_enriched),
        ),
        runner.compare(
            name="adaptive_duplicate_aggregation",
            before=_aqe_action(ecdc),
            after=_aqe_action(ecdc),
            correctness_before=lambda: _single_output(
                "duplicate_aggregation", _duplicate_aggregation(ecdc)
            ),
            correctness_after=lambda: _single_output(
                "duplicate_aggregation", _duplicate_aggregation(ecdc)
            ),
            before_configuration={"spark.sql.adaptive.enabled": "false"},
            after_configuration={"spark.sql.adaptive.enabled": "true"},
        ),
        runner.compare(
            name="reused_frame_cache",
            before=_cache_action(ecdc, cache_enabled=False),
            after=_cache_action(ecdc, cache_enabled=True),
            correctness_before=lambda: _cache_correctness(ecdc, cache_enabled=False),
            correctness_after=lambda: _cache_correctness(ecdc, cache_enabled=True),
        ),
    ]
    spark.conf.set("spark.sql.adaptive.enabled", "true")
    optimized_enriched.count()
    baseline_enriched.count()
    optimized_plan = plan_evidence(optimized_enriched)
    baseline_plan = plan_evidence(baseline_enriched)
    context_equivalence = _country_context_equivalence(
        optimized_enriched,
        snowflake_context_fingerprint,
    )
    if optimized_plan["broadcast_hash_join_count"] < 3:
        raise SparkPipelineError(
            "Optimized plan did not use all three broadcast joins."
        )
    if optimized_plan["build_right_count"] < 3:
        raise SparkPipelineError("Optimized joins did not build all right dimensions.")
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
            correctness_before=lambda: _write_correctness(
                spark,
                optimized_enriched,
                scratch_root,
                partitions=8,
                repartition=True,
            ),
            correctness_after=lambda: _write_correctness(
                spark,
                optimized_enriched,
                scratch_root,
                partitions=layout["target_file_count"],
                repartition=False,
            ),
        )
    )
    return BenchmarkSuiteResult(
        benchmarks=benchmarks,
        curated=optimized_enriched,
        layout=layout,
        plans={
            "baseline": baseline_plan,
            "optimized": optimized_plan,
        },
        context_equivalence=context_equivalence,
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
    context_equivalence: dict[str, Any],
) -> dict[str, Any]:
    compact_plans = {
        name: {key: value for key, value in plan.items() if key != "normalized_plan"}
        for name, plan in plans.items()
    }
    return {
        "evidence_version": 3,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "fingerprint_protocol": FINGERPRINT_PROTOCOL,
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
            "world_bank_snapshot_id": source_manifest.get("world_bank_snapshot_id"),
        },
        "ingestion_id": ingestion_id,
        "benchmark_run_id": benchmark_run_id,
        "cold_end_to_end_ms": cold_end_to_end_ms,
        "quality": {
            "ruleset_version": quality["ruleset_version"],
            "status": quality["status"],
            "quality_document_sha256": quality["quality_document_sha256"],
        },
        "layout": layout,
        "plans": compact_plans,
        "country_context_equivalence": context_equivalence,
        "benchmarks": benchmarks,
        "conclusions": _conclusions(benchmarks),
        "limitations": [
            "The workload is too small for Spark throughput benefits.",
            "Measurements are local-mode results and do not represent a cluster.",
            "Warm runs reduce startup and JIT noise but cannot remove filesystem-cache effects.",
            "Snowflake SQL remains the production semantic layer.",
        ],
    }


def _quality_from_bronze_manifest(manifest: dict[str, Any]) -> dict[str, str]:
    if manifest.get("manifest_version") != 2:
        raise SparkPipelineError(
            "Benchmark-only mode requires Bronze manifest version 2."
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

    bronze_target = bronze_root / f"ingestion_id={ingestion_id}"
    curated_target = curated_root / f"ingestion_id={ingestion_id}"
    if bronze_target.exists() or curated_target.exists():
        raise FileExistsError(f"Ingestion ID already exists: {ingestion_id}")

    event_log_directory = run_output / "eventlog"
    spark = create_spark_session(
        application_name=f"covid-bronze-{ingestion_id}",
        event_log_directory=event_log_directory,
    )
    frames: dict[str, DataFrame] = {}
    try:
        environment = _environment(spark)
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
        )
        _write_json(quality_path, quality)
        if assessment["status"] == "FAIL":
            raise QualityFailure("Quality failures blocked curated publication.")

        suite = _benchmark_suite(
            spark,
            frames,
            benchmark_run_id=benchmark_run_id,
            run_output=run_output,
            snowflake_context_fingerprint=source_manifest.get(
                "snowflake_context_fingerprint"
            ),
        )
        curated_metrics = _write_curated(
            suite.curated,
            curated_root=curated_root,
            ingestion_id=ingestion_id,
            layout=suite.layout,
        )
        suite.layout["published_output"] = curated_metrics
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
    cold_end_to_end_ms = round((time.perf_counter() - started) * 1000, 3)
    optimization = {
        "benchmark_run_id": benchmark_run_id,
        "cold_end_to_end_ms": cold_end_to_end_ms,
        "environment": environment,
        "layout": suite.layout,
        "plans": suite.plans,
        "benchmarks": benchmarks,
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
    )
    _write_json_atomic(evidence_path, evidence)
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
    quality = _quality_from_bronze_manifest(bronze_manifest)
    run_output = output_root / benchmark_run_id
    if run_output.exists():
        raise FileExistsError(f"Benchmark run already exists: {benchmark_run_id}")
    event_log_directory = run_output / "eventlog"
    spark = create_spark_session(
        application_name=f"covid-benchmark-{benchmark_run_id}",
        event_log_directory=event_log_directory,
    )
    frames: dict[str, DataFrame] = {}
    try:
        environment = _environment(spark)
        frames = _read_bronze(spark, ingestion_directory)
        suite = _benchmark_suite(
            spark,
            frames,
            benchmark_run_id=benchmark_run_id,
            run_output=run_output,
            snowflake_context_fingerprint=bronze_manifest.get(
                "snowflake_context_fingerprint"
            ),
        )
    finally:
        _release_spark_resources(spark, frames)
    benchmarks = summarize_benchmarks(
        suite.benchmarks,
        parse_event_logs(event_log_directory),
    )
    source_manifest = {
        "source_batch_id": bronze_manifest["source_batch_id"],
        "batch_sha256": bronze_manifest["source_batch_sha256"],
        "files": bronze_manifest["source_files"],
        "world_bank_snapshot_id": bronze_manifest.get("world_bank_snapshot_id"),
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
    )
    _write_json(run_output / "optimization_metrics.json", evidence)
    _write_json_atomic(evidence_path, evidence)
