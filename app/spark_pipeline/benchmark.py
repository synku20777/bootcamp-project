from __future__ import annotations

import hashlib
import json
import re
import statistics
import time
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T

MEASURED_REPETITIONS = 5
TARGET_FILE_BYTES = 128 * 1024 * 1024
MIN_MONTH_PARTITION_BYTES = 64 * 1024 * 1024
MAX_MONTH_DIRECTORIES = 240
FINGERPRINT_PROTOCOL = {
    "fingerprint_version": 1,
    "canonicalization": "spark-scalar-json-v1",
    "algorithm": "sha256-row-multiset-v1",
}
_CANONICAL_JSON_OPTIONS = {
    "ignoreNullFields": "false",
    "dateFormat": "yyyy-MM-dd",
    "timestampFormat": "yyyy-MM-dd'T'HH:mm:ss.SSSSSSXXX",
}
_SUPPORTED_SCALAR_TYPES = (
    T.StringType,
    T.BooleanType,
    T.ByteType,
    T.ShortType,
    T.IntegerType,
    T.LongType,
    T.FloatType,
    T.DoubleType,
    T.DecimalType,
    T.DateType,
    T.TimestampType,
)
if hasattr(T, "TimestampNTZType"):
    _SUPPORTED_SCALAR_TYPES = (*_SUPPORTED_SCALAR_TYPES, T.TimestampNTZType)


class BenchmarkCorrectnessError(RuntimeError):
    """A benchmark variant produced a different logical result."""


@dataclass(slots=True)
class CorrectnessOutputs:
    datasets: dict[str, DataFrame]
    cleanup: Callable[[], None] = lambda: None


def _canonical_schema(dataframe: DataFrame) -> list[dict[str, Any]]:
    names = [field.name for field in dataframe.schema.fields]
    if len(names) != len(set(names)):
        raise BenchmarkCorrectnessError(
            "Benchmark correctness does not support duplicate column names."
        )
    schema = []
    for field in dataframe.schema.fields:
        if not isinstance(field.dataType, _SUPPORTED_SCALAR_TYPES):
            raise BenchmarkCorrectnessError(
                "Benchmark correctness encountered an unsupported Spark data type."
            )
        schema.append(
            {
                "name": field.name,
                "type": field.dataType.jsonValue(),
                "nullable": field.nullable,
                "metadata": field.metadata,
            }
        )
    return schema


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def dataframe_fingerprint(dataframe: DataFrame) -> dict[str, Any]:
    schema = _canonical_schema(dataframe)
    schema_json = _canonical_json(schema)
    fields = []
    for index, field in enumerate(dataframe.schema.fields):
        column = dataframe[field.name]
        value_json = F.when(
            column.isNotNull(),
            F.to_json(
                F.struct(column.alias("value")),
                options=_CANONICAL_JSON_OPTIONS,
            ),
        )
        fields.append(
            F.struct(
                F.lit(field.name).alias("name"),
                F.lit(_canonical_json(field.dataType.jsonValue())).alias("type"),
                column.isNull().alias("is_null"),
                value_json.alias("value_json"),
            ).alias(f"field_{index}")
        )
    canonical_row = F.to_json(F.struct(*fields), options=_CANONICAL_JSON_OPTIONS)
    row_hash_counts = (
        dataframe.select(F.sha2(canonical_row, 256).alias("row_sha256"))
        .groupBy("row_sha256")
        .count()
        .orderBy("row_sha256")
    )
    content_digest = hashlib.sha256()
    row_count = 0
    distinct_row_hash_count = 0
    for row in row_hash_counts.toLocalIterator():
        occurrence_count = int(row["count"])
        if occurrence_count < 0 or occurrence_count >= 2**64:
            raise BenchmarkCorrectnessError(
                "Benchmark row occurrence count exceeds the fingerprint protocol."
            )
        content_digest.update(bytes.fromhex(row["row_sha256"]))
        content_digest.update(occurrence_count.to_bytes(8, "big", signed=False))
        row_count += occurrence_count
        distinct_row_hash_count += 1
    return {
        "schema": schema,
        "schema_sha256": hashlib.sha256(schema_json.encode("utf-8")).hexdigest(),
        "row_count": row_count,
        "distinct_row_hash_count": distinct_row_hash_count,
        "content_sha256": content_digest.hexdigest(),
    }


@contextmanager
def spark_configuration(
    spark: SparkSession,
    settings: dict[str, str],
) -> Iterator[None]:
    previous = {name: spark.conf.get(name, None) for name in settings}
    try:
        for name, value in settings.items():
            spark.conf.set(name, value)
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                spark.conf.unset(name)
            else:
                spark.conf.set(name, value)


def directory_metrics(path: Path) -> dict[str, int]:
    files = [item for item in path.rglob("part-*") if item.is_file()]
    return {
        "output_file_count": len(files),
        "output_bytes": sum(item.stat().st_size for item in files),
    }


def layout_decision(
    *,
    measured_parquet_bytes: int,
    monthly_parquet_bytes: list[int],
    target_file_bytes: int = TARGET_FILE_BYTES,
) -> dict[str, Any]:
    month_count = len(monthly_parquet_bytes)
    median_month_bytes = (
        int(statistics.median(monthly_parquet_bytes)) if monthly_parquet_bytes else 0
    )
    partition_by_month = (
        month_count >= 2
        and month_count <= MAX_MONTH_DIRECTORIES
        and median_month_bytes >= MIN_MONTH_PARTITION_BYTES
    )
    target_files = max(1, -(-measured_parquet_bytes // target_file_bytes))
    return {
        "partition_columns": (
            ["report_year", "report_month"] if partition_by_month else []
        ),
        "target_file_bytes": target_file_bytes,
        "target_file_count": target_files,
        "measured_parquet_bytes": measured_parquet_bytes,
        "month_count": month_count,
        "median_month_parquet_bytes": median_month_bytes,
        "reason": (
            "monthly partitions satisfy access-pattern and size criteria"
            if partition_by_month
            else "unpartitioned output avoids tiny month directories"
        ),
    }


def sanitized_plan(plan: str) -> str:
    plan = re.sub(r"file:[^\s,]+", "file:<redacted>", plan)
    plan = re.sub(r"#[0-9]+[L]?", "#<id>", plan)
    plan = re.sub(r"plan_id=[0-9]+", "plan_id=<id>", plan)
    return plan


def plan_evidence(dataframe: DataFrame) -> dict[str, Any]:
    complete_plan = sanitized_plan(
        dataframe._jdf.queryExecution().executedPlan().toString()
    )
    adaptive_final = "isFinalPlan=true" in complete_plan
    plan = complete_plan
    if "== Final Plan ==" in complete_plan:
        plan = complete_plan.split("== Final Plan ==", maxsplit=1)[1]
        plan = plan.split("== Initial Plan ==", maxsplit=1)[0].strip()
    lines = list(
        dict.fromkeys(line.strip() for line in plan.splitlines() if "Join" in line)
    )
    broadcast_lines = [line for line in lines if "BroadcastHashJoin" in line]
    null_safe_mapping = any(
        "source_code_key" in line
        and "mapping_code_key" in line
        and "isnull(source_code_key" in line
        and "isnull(mapping_code_key" in line
        for line in lines
    )
    return {
        "sha256": hashlib.sha256(plan.encode("utf-8")).hexdigest(),
        "adaptive_final": adaptive_final,
        "broadcast_hash_join_count": len(broadcast_lines),
        "build_right_count": sum("BuildRight" in line for line in broadcast_lines),
        "null_safe_mapping_key_present": null_safe_mapping,
        "typed_population_key_present": "population_lookup_key" in plan,
        "join_operators": lines,
        "normalized_plan": plan,
    }


class BenchmarkRunner:
    def __init__(
        self,
        spark: SparkSession,
        benchmark_run_id: str,
        correctness_failure_path: Path,
    ) -> None:
        self.spark = spark
        self.benchmark_run_id = benchmark_run_id
        self.correctness_failure_path = correctness_failure_path

    def _run_once(
        self,
        benchmark: str,
        variant: str,
        repetition: int,
        action: Callable[[], dict[str, Any] | None],
        configuration: dict[str, str],
    ) -> dict[str, Any]:
        job_group = f"{self.benchmark_run_id}:{benchmark}:{variant}:{repetition}"
        self.spark.sparkContext.setJobGroup(job_group, job_group)
        started = time.perf_counter()
        try:
            with spark_configuration(self.spark, configuration):
                action_metrics = action() or {}
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
        finally:
            self.spark.sparkContext.setJobGroup("", "")
        return {
            "repetition": repetition,
            "job_group": job_group,
            "duration_ms": duration_ms,
            **action_metrics,
        }

    def compare(
        self,
        *,
        name: str,
        before: Callable[[], dict[str, Any] | None],
        after: Callable[[], dict[str, Any] | None],
        correctness_before: Callable[[], CorrectnessOutputs],
        correctness_after: Callable[[], CorrectnessOutputs],
        before_configuration: dict[str, str] | None = None,
        after_configuration: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        before_configuration = before_configuration or {}
        after_configuration = after_configuration or {}
        correctness = self._correctness_gate(
            name=name,
            before=correctness_before,
            after=correctness_after,
            before_configuration=before_configuration,
            after_configuration=after_configuration,
        )
        self._run_once(name, "before", -1, before, before_configuration)
        self._run_once(name, "after", -1, after, after_configuration)
        measurements: dict[str, list[dict[str, Any]]] = {
            "before": [],
            "after": [],
        }
        for repetition in range(MEASURED_REPETITIONS):
            order = ("before", "after") if repetition % 2 == 0 else ("after", "before")
            for variant in order:
                action = before if variant == "before" else after
                configuration = (
                    before_configuration if variant == "before" else after_configuration
                )
                measurements[variant].append(
                    self._run_once(
                        name,
                        variant,
                        repetition,
                        action,
                        configuration,
                    )
                )
        return {
            "name": name,
            "correctness": correctness,
            "warmup_repetitions": 1,
            "measured_repetitions": MEASURED_REPETITIONS,
            "measurements": measurements,
        }

    def _fingerprint_outputs(
        self,
        *,
        benchmark: str,
        variant: str,
        builder: Callable[[], CorrectnessOutputs],
        configuration: dict[str, str],
    ) -> dict[str, dict[str, Any]]:
        job_group = f"{self.benchmark_run_id}:{benchmark}:correctness:{variant}"
        self.spark.sparkContext.setJobGroup(job_group, job_group)
        outputs: CorrectnessOutputs | None = None
        try:
            with spark_configuration(self.spark, configuration):
                outputs = builder()
                return {
                    name: dataframe_fingerprint(dataframe)
                    for name, dataframe in outputs.datasets.items()
                }
        finally:
            if outputs is not None:
                outputs.cleanup()
            self.spark.sparkContext.setJobGroup("", "")

    def _correctness_gate(
        self,
        *,
        name: str,
        before: Callable[[], CorrectnessOutputs],
        after: Callable[[], CorrectnessOutputs],
        before_configuration: dict[str, str],
        after_configuration: dict[str, str],
    ) -> dict[str, Any]:
        try:
            before_fingerprints = self._fingerprint_outputs(
                benchmark=name,
                variant="before",
                builder=before,
                configuration=before_configuration,
            )
            after_fingerprints = self._fingerprint_outputs(
                benchmark=name,
                variant="after",
                builder=after,
                configuration=after_configuration,
            )
        finally:
            self.spark.catalog.clearCache()

        dataset_names = sorted(set(before_fingerprints) | set(after_fingerprints))
        datasets = []
        for dataset_name in dataset_names:
            before_fingerprint = before_fingerprints.get(dataset_name)
            after_fingerprint = after_fingerprints.get(dataset_name)
            schema_match = (
                before_fingerprint is not None
                and after_fingerprint is not None
                and before_fingerprint["schema_sha256"]
                == after_fingerprint["schema_sha256"]
            )
            row_count_match = (
                before_fingerprint is not None
                and after_fingerprint is not None
                and before_fingerprint["row_count"] == after_fingerprint["row_count"]
            )
            content_checksum_match = (
                before_fingerprint is not None
                and after_fingerprint is not None
                and before_fingerprint["content_sha256"]
                == after_fingerprint["content_sha256"]
            )
            datasets.append(
                {
                    "dataset": dataset_name,
                    "before": before_fingerprint,
                    "after": after_fingerprint,
                    "schema_match": schema_match,
                    "row_count_match": row_count_match,
                    "content_checksum_match": content_checksum_match,
                    "passed": (
                        schema_match and row_count_match and content_checksum_match
                    ),
                }
            )
        result = {
            "status": "PASS" if all(item["passed"] for item in datasets) else "FAIL",
            "fingerprint_protocol": FINGERPRINT_PROTOCOL,
            "datasets": datasets,
        }
        if result["status"] == "FAIL":
            self._write_correctness_failure(name, result)
            raise BenchmarkCorrectnessError(
                f"Benchmark correctness gate failed: {name}"
            )
        return result

    def _write_correctness_failure(
        self,
        benchmark_name: str,
        correctness: dict[str, Any],
    ) -> None:
        payload = {
            "failure_version": 1,
            "status": "FAIL",
            "benchmark_run_id": self.benchmark_run_id,
            "benchmark_name": benchmark_name,
            **correctness,
        }
        self.correctness_failure_path.parent.mkdir(parents=True, exist_ok=True)
        self.correctness_failure_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "median": round(statistics.median(values), 3),
        "minimum": round(min(values), 3),
        "maximum": round(max(values), 3),
    }


def summarize_benchmarks(
    benchmarks: list[dict[str, Any]],
    event_metrics: dict[str, dict[str, int]],
) -> list[dict[str, Any]]:
    for benchmark in benchmarks:
        for variant, measurements in benchmark["measurements"].items():
            for measurement in measurements:
                measurement.update(event_metrics.get(measurement["job_group"], {}))
            durations = [item["duration_ms"] for item in measurements]
            benchmark.setdefault("summary", {})[variant] = {
                "duration_ms": _summary(durations),
                "shuffle_read_bytes": _summary(
                    [item.get("shuffle_read_bytes", 0) for item in measurements]
                ),
                "shuffle_write_bytes": _summary(
                    [item.get("shuffle_write_bytes", 0) for item in measurements]
                ),
                "input_bytes": _summary(
                    [item.get("input_bytes", 0) for item in measurements]
                ),
                "memory_bytes_spilled": _summary(
                    [item.get("memory_bytes_spilled", 0) for item in measurements]
                ),
                "disk_bytes_spilled": _summary(
                    [item.get("disk_bytes_spilled", 0) for item in measurements]
                ),
                "peak_execution_memory_bytes": _summary(
                    [
                        item.get("peak_execution_memory_bytes", 0)
                        for item in measurements
                    ]
                ),
                "executor_run_time_ms": _summary(
                    [item.get("executor_run_time_ms", 0) for item in measurements]
                ),
                "jvm_gc_time_ms": _summary(
                    [item.get("jvm_gc_time_ms", 0) for item in measurements]
                ),
            }
    return benchmarks


def parse_event_logs(event_log_directory: Path) -> dict[str, dict[str, int]]:
    stage_groups: dict[int, set[str]] = defaultdict(set)
    stage_metrics: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for path in event_log_directory.rglob("*"):
        if not path.is_file() or path.name.startswith("."):
            continue
        with path.open("r", encoding="utf-8", errors="replace") as source:
            for line in source:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_name = event.get("Event")
                if event_name == "SparkListenerJobStart":
                    properties = event.get("Properties", {}) or {}
                    group = properties.get("spark.jobGroup.id")
                    if group:
                        for stage_id in event.get("Stage IDs", []):
                            stage_groups[int(stage_id)].add(group)
                elif event_name == "SparkListenerTaskEnd":
                    stage_id = int(event.get("Stage ID", -1))
                    metrics = event.get("Task Metrics", {}) or {}
                    shuffle_read = metrics.get("Shuffle Read Metrics", {}) or {}
                    shuffle_write = metrics.get("Shuffle Write Metrics", {}) or {}
                    input_metrics = metrics.get("Input Metrics", {}) or {}
                    stage_metrics[stage_id]["shuffle_read_bytes"] += int(
                        shuffle_read.get("Remote Bytes Read", 0)
                        + shuffle_read.get("Local Bytes Read", 0)
                    )
                    stage_metrics[stage_id]["shuffle_write_bytes"] += int(
                        shuffle_write.get("Shuffle Bytes Written", 0)
                    )
                    stage_metrics[stage_id]["input_bytes"] += int(
                        input_metrics.get("Bytes Read", 0)
                    )
                    stage_metrics[stage_id]["memory_bytes_spilled"] += int(
                        metrics.get("Memory Bytes Spilled", 0)
                    )
                    stage_metrics[stage_id]["disk_bytes_spilled"] += int(
                        metrics.get("Disk Bytes Spilled", 0)
                    )
                    stage_metrics[stage_id]["executor_run_time_ms"] += int(
                        metrics.get("Executor Run Time", 0)
                    )
                    stage_metrics[stage_id]["jvm_gc_time_ms"] += int(
                        metrics.get("JVM GC Time", 0)
                    )
                    stage_metrics[stage_id]["peak_execution_memory_bytes"] = max(
                        stage_metrics[stage_id]["peak_execution_memory_bytes"],
                        int(metrics.get("Peak Execution Memory", 0)),
                    )

    group_metrics: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    all_metrics: dict[str, int] = defaultdict(int)
    for metrics in stage_metrics.values():
        for metric, value in metrics.items():
            if metric == "peak_execution_memory_bytes":
                all_metrics[metric] = max(all_metrics[metric], value)
            else:
                all_metrics[metric] += value
    for stage_id, groups in stage_groups.items():
        for group in groups:
            for metric, value in stage_metrics[stage_id].items():
                if metric == "peak_execution_memory_bytes":
                    group_metrics[group][metric] = max(
                        group_metrics[group][metric], value
                    )
                else:
                    group_metrics[group][metric] += value
    result = {group: dict(metrics) for group, metrics in group_metrics.items()}
    result["__all__"] = dict(all_metrics)
    return result
