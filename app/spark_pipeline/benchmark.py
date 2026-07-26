from __future__ import annotations

import hashlib
import json
import re
import statistics
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pyspark.sql import DataFrame, SparkSession

MEASURED_REPETITIONS = 5
TARGET_FILE_BYTES = 128 * 1024 * 1024
MIN_MONTH_PARTITION_BYTES = 64 * 1024 * 1024
MAX_MONTH_DIRECTORIES = 240


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
    target_files = max(1, -(-measured_parquet_bytes // TARGET_FILE_BYTES))
    return {
        "partition_columns": (
            ["report_year", "report_month"] if partition_by_month else []
        ),
        "target_file_bytes": TARGET_FILE_BYTES,
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
        for line in broadcast_lines
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
    ) -> None:
        self.spark = spark
        self.benchmark_run_id = benchmark_run_id

    def _run_once(
        self,
        benchmark: str,
        variant: str,
        repetition: int,
        action: Callable[[], dict[str, Any] | None],
    ) -> dict[str, Any]:
        job_group = f"{self.benchmark_run_id}:{benchmark}:{variant}:{repetition}"
        self.spark.sparkContext.setJobGroup(job_group, job_group)
        started = time.perf_counter()
        action_metrics = action() or {}
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
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
    ) -> dict[str, Any]:
        self._run_once(name, "before", -1, before)
        self._run_once(name, "after", -1, after)
        measurements: dict[str, list[dict[str, Any]]] = {
            "before": [],
            "after": [],
        }
        for repetition in range(MEASURED_REPETITIONS):
            order = ("before", "after") if repetition % 2 == 0 else ("after", "before")
            for variant in order:
                action = before if variant == "before" else after
                measurements[variant].append(
                    self._run_once(name, variant, repetition, action)
                )
        return {
            "name": name,
            "warmup_repetitions": 1,
            "measured_repetitions": MEASURED_REPETITIONS,
            "measurements": measurements,
        }


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

    group_metrics: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for stage_id, groups in stage_groups.items():
        for group in groups:
            for metric, value in stage_metrics[stage_id].items():
                group_metrics[group][metric] += value
    return {group: dict(metrics) for group, metrics in group_metrics.items()}
