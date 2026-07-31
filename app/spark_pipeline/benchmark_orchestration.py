"""Benchmark scenario construction, correctness gates, and layout calibration.

This module owns comparisons between baseline and optimized Spark plans.  It
keeps benchmark-specific actions and temporary outputs away from the job
harness while returning the exact ``BenchmarkSuiteResult`` contract formerly
constructed in ``pipeline``.  Transformation definitions remain in
``transformations`` and low-level timing/fingerprinting remains in
``benchmark``.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from pyspark import StorageLevel
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from app.country_context_fingerprint import country_context_fingerprint
from app.spark_pipeline.benchmark import (
    BenchmarkRunner,
    CorrectnessOutputs,
    directory_metrics,
    layout_decision,
    plan_evidence,
)
from app.spark_pipeline.clustering import country_key_skew
from app.spark_pipeline.contracts import BenchmarkSuiteResult, SparkPipelineError
from app.spark_pipeline.runtime_policy import SparkRuntimePolicy
from app.spark_pipeline.transformations import (
    context_eligible_country_baseline,
    country_baseline,
    enrich_with_country_context,
    enrich_with_population,
    normalized_daily,
)


def _count_action(dataframe: DataFrame) -> Any:
    """Create a deferred benchmark action that materializes a DataFrame count."""

    def action() -> dict[str, Any]:
        return {
            "row_count": dataframe.count(),
            "partitions": dataframe.rdd.getNumPartitions(),
        }

    return action


def _single_output(name: str, dataframe: DataFrame) -> CorrectnessOutputs:
    """Wrap one logical result in the benchmark fingerprinting contract."""

    return CorrectnessOutputs({name: dataframe})


def _country_context_equivalence(
    context_baseline: DataFrame,
    enriched_metrics: dict[str, int],
    expected: dict[str, Any] | None,
) -> dict[str, Any]:
    """Verify Spark context rows against the cross-platform source fingerprint.

    The baseline is bounded to one row per eligible country, so streaming it
    through ``toLocalIterator`` is safe and avoids both a full driver-side
    ``collect`` allocation and a second Spark distinct action.
    """

    projection = context_baseline.select(
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
    # This projection is bounded to one row per eligible country. Materialize
    # it once because the shared Python fingerprint protocol needs the rows and
    # a second Spark DISTINCT action would add work without improving scale.
    projection_rows = [
        row.asDict(recursive=True) for row in projection.toLocalIterator()
    ]
    actual = country_context_fingerprint(projection_rows)
    matches = expected is None or actual == expected
    if not matches:
        raise SparkPipelineError(
            "Spark country context does not match the Snowflake baseline fingerprint."
        )
    snapshot_ids = sorted(
        {str(row["SNAPSHOT_ID"]) for row in projection_rows if row["SNAPSHOT_ID"]}
    )
    return {
        "snowflake": expected,
        "spark": actual,
        "fingerprints_match": matches,
        "snapshot_ids": snapshot_ids,
        "baseline_rows": actual["row_count"],
        "baseline_projection_canonical_bytes": actual["canonical_bytes"],
        **enriched_metrics,
    }


def _enriched_context_metrics(enriched: DataFrame) -> dict[str, int]:
    """Collect joined-row and unmatched-location counts in one Spark action."""

    row = enriched.agg(
        F.count(F.lit(1)).alias("joined_covid_rows"),
        F.countDistinct(
            F.when(
                F.col("context_snapshot_id").isNull(),
                F.col("location_key"),
            )
        ).alias("unmatched_location_count"),
    ).first()
    return {
        "joined_covid_rows": int(row["joined_covid_rows"]),
        "unmatched_location_count": int(row["unmatched_location_count"]),
    }


def _projected_input(ecdc: DataFrame) -> DataFrame:
    """Build the lazy early-projection/filter plan used by the benchmark."""

    return ecdc.select(
        "COUNTRY_REGION",
        "ISO3166_1",
        "REPORT_DATE",
        "CASES",
        "DEATHS",
    ).where(F.col("COUNTRY_REGION").isNotNull() & F.col("REPORT_DATE").isNotNull())


def _projection_result(dataframe: DataFrame) -> DataFrame:
    """Return the aggregate that proves projection preserves business totals."""

    return dataframe.agg(
        F.count(F.lit(1)).alias("rows"),
        F.sum(F.coalesce("CASES", F.lit(0))).alias("cases"),
        F.sum(F.coalesce("DEATHS", F.lit(0))).alias("deaths"),
    )


def _projection_action(dataframe: DataFrame) -> Any:
    """Create a deferred action measuring projection workload shape."""

    def action() -> dict[str, Any]:
        row = _projection_result(dataframe).first()
        return {
            "row_count": int(row["rows"]),
            "column_count": len(dataframe.columns),
            "partitions": dataframe.rdd.getNumPartitions(),
        }

    return action


def _duplicate_aggregation(ecdc: DataFrame) -> DataFrame:
    """Build the duplicate-key aggregation used to compare AQE settings."""

    return (
        ecdc.select("COUNTRY_REGION", "REPORT_DATE", "CASES", "DEATHS")
        .groupBy("COUNTRY_REGION", "REPORT_DATE")
        .agg(
            F.sum("CASES").alias("cases"),
            F.sum("DEATHS").alias("deaths"),
        )
    )


def _aqe_action(ecdc: DataFrame) -> Any:
    """Create a deferred aggregation action whose plan responds to AQE config."""

    def action() -> dict[str, Any]:
        aggregation = _duplicate_aggregation(ecdc)
        row_count = aggregation.count()
        return {
            "row_count": row_count,
            "partitions": aggregation.rdd.getNumPartitions(),
        }

    return action


def _cache_input(ecdc: DataFrame) -> DataFrame:
    """Select the reusable lazy input shared by both cache benchmark branches."""

    return ecdc.select("COUNTRY_REGION", "REPORT_DATE", "CASES", "DEATHS")


def _cache_results(reused: DataFrame) -> dict[str, DataFrame]:
    """Build two independent consumers that make reuse measurable."""

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
    """Create a deferred two-consumer action with optional disk-backed caching.

    The warm-up count is mandatory when caching: ``persist`` alone is lazy and
    would otherwise charge cache population to the first measured consumer.
    Unpersist runs in ``finally`` so benchmark failures do not leak executor
    memory into subsequent scenarios.
    """

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
    """Expose named cache outputs and defer cache cleanup to the fingerprint gate."""

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
    """Write one temporary Parquet layout and return its physical metrics.

    Repartitioning models a full-shuffle baseline; coalescing models the
    optimized shrink-only layout.  The caller selects the strategy explicitly
    so the benchmark cannot silently change semantics.
    """

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
    """Create a deferred temporary-write action with guaranteed directory cleanup."""

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
    """Write and read back Parquet so correctness covers serialization schema.

    The temporary directory remains alive until ``CorrectnessOutputs.cleanup``
    runs because the returned DataFrame is lazy and still references its files.
    """

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


def _calibrate_layout(
    dataframe: DataFrame,
    root: Path,
    *,
    target_file_bytes: int,
) -> dict[str, Any]:
    """Measure candidate Parquet layouts and derive the curated write policy.

    Calibration intentionally performs real writes: estimated logical sizes do
    not capture compression or small partition effects.  Scratch data is always
    removed because only the resulting driver-side metrics are durable.
    """

    target = root / "calibration"
    total_target = target / "unpartitioned"
    monthly_target = target / "monthly"
    try:
        dataframe.repartition(8).write.mode("error").parquet(str(total_target))
        with_month = dataframe.withColumn(
            "report_year", F.year("report_date")
        ).withColumn("report_month", F.month("report_date"))
        (
            with_month.repartition("report_year", "report_month")
            .write.mode("error")
            .partitionBy("report_year", "report_month")
            .parquet(str(monthly_target))
        )
        monthly_bytes = [
            directory_metrics(month_directory)["output_bytes"]
            for year_directory in monthly_target.glob("report_year=*")
            for month_directory in year_directory.glob("report_month=*")
        ]
        metrics = directory_metrics(total_target)
        decision = layout_decision(
            measured_parquet_bytes=metrics["output_bytes"],
            monthly_parquet_bytes=monthly_bytes,
            target_file_bytes=target_file_bytes,
        )
        decision["monthly_measurement"] = "actual_partition_directories"
        return decision
    finally:
        shutil.rmtree(target, ignore_errors=True)


def _benchmark_suite(
    spark: SparkSession,
    frames: dict[str, DataFrame],
    *,
    benchmark_run_id: str,
    run_output: Path,
    snowflake_context_fingerprint: dict[str, Any] | None,
    policy: SparkRuntimePolicy,
    broadcast_decisions: dict[str, bool],
) -> BenchmarkSuiteResult:
    """Run correctness-gated optimization comparisons and return curated output.

    The returned curated DataFrame remains lazy; publication is owned by the
    outer job harness.  Baseline and optimized plans use identical business
    transformations, varying only the optimization under measurement, which
    prevents benchmark improvements from masking schema or value changes.
    """

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
    optimized_daily = normalized_daily(
        ecdc,
        mapping,
        broadcast_mapping=broadcast_decisions["mapping"],
    )
    baseline_population_enriched = enrich_with_population(
        baseline_daily,
        population,
        broadcast_population=False,
    )
    optimized_population_enriched = enrich_with_population(
        optimized_daily,
        population,
        broadcast_population=broadcast_decisions["population"],
    )
    baseline_enriched = enrich_with_country_context(
        baseline_population_enriched,
        baseline,
        broadcast_baseline=False,
    )
    optimized_enriched = enrich_with_country_context(
        optimized_population_enriched,
        baseline,
        broadcast_baseline=broadcast_decisions["indicators"],
    )
    context_baseline = context_eligible_country_baseline(
        optimized_population_enriched,
        baseline,
        broadcast_baseline=broadcast_decisions["indicators"],
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
    # Collect both audit metrics in one post-benchmark action. Persisting the
    # frame here would make the later file-layout comparison measure a warmed
    # cache instead of the declared layouts, and this workload did not justify
    # that memory tradeoff in its dedicated cache benchmark.
    optimized_metrics = _enriched_context_metrics(optimized_enriched)
    baseline_enriched.count()
    optimized_plan = plan_evidence(optimized_enriched)
    baseline_plan = plan_evidence(baseline_enriched)
    context_equivalence = _country_context_equivalence(
        context_baseline,
        optimized_metrics,
        snowflake_context_fingerprint,
    )
    if not optimized_plan["adaptive_final"]:
        raise SparkPipelineError("Optimized plan did not reach its final AQE state.")
    expected_broadcasts = sum(broadcast_decisions.values())
    if optimized_plan["broadcast_hash_join_count"] != expected_broadcasts:
        raise SparkPipelineError(
            "Optimized plan does not match the size-gated broadcast decisions."
        )
    if optimized_plan["build_right_count"] != expected_broadcasts:
        raise SparkPipelineError(
            "Optimized joins did not build the selected dimensions."
        )
    if not optimized_plan["null_safe_mapping_key_present"]:
        raise SparkPipelineError("Mapping plan does not show null-safe key matching.")
    if not optimized_plan["typed_population_key_present"]:
        raise SparkPipelineError("Population plan does not show the typed lookup key.")

    scratch_root.mkdir(parents=True, exist_ok=True)
    layout = _calibrate_layout(
        optimized_enriched,
        scratch_root,
        target_file_bytes=policy.target_file_bytes,
    )
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
        broadcast_decisions=broadcast_decisions,
        skew=country_key_skew(
            frames["covid_extended"],
            ratio_warn=policy.skew_ratio_warn,
            share_warn=policy.skew_share_warn,
        ),
    )
