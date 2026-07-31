from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.spark_pipeline.benchmark import parse_event_logs, summarize_benchmarks


class SparkEventMetricTests(unittest.TestCase):
    def test_event_log_includes_spill_memory_runtime_and_gc_metrics(self) -> None:
        events = [
            {
                "Event": "SparkListenerJobStart",
                "Stage IDs": [4],
                "Properties": {"spark.jobGroup.id": "fixture:before:0"},
            },
            {
                "Event": "SparkListenerTaskEnd",
                "Stage ID": 4,
                "Task Metrics": {
                    "Memory Bytes Spilled": 11,
                    "Disk Bytes Spilled": 12,
                    "Executor Run Time": 13,
                    "JVM GC Time": 14,
                    "Peak Execution Memory": 15,
                    "Shuffle Read Metrics": {
                        "Remote Bytes Read": 16,
                        "Local Bytes Read": 17,
                    },
                    "Shuffle Write Metrics": {"Shuffle Bytes Written": 18},
                    "Input Metrics": {"Bytes Read": 19},
                },
            },
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            event_log = Path(temporary_directory) / "events"
            event_log.write_text(
                "".join(f"{json.dumps(event)}\n" for event in events),
                encoding="utf-8",
            )
            metrics = parse_event_logs(Path(temporary_directory))

        expected = {
            "shuffle_read_bytes": 33,
            "shuffle_write_bytes": 18,
            "input_bytes": 19,
            "memory_bytes_spilled": 11,
            "disk_bytes_spilled": 12,
            "executor_run_time_ms": 13,
            "jvm_gc_time_ms": 14,
            "peak_execution_memory_bytes": 15,
        }
        self.assertEqual(metrics["fixture:before:0"], expected)
        self.assertEqual(metrics["__all__"], expected)

        benchmark = {
            "name": "fixture",
            "measurements": {
                "before": [
                    {
                        "job_group": "fixture:before:0",
                        "duration_ms": 1.0,
                    }
                ],
                "after": [
                    {
                        "job_group": "fixture:after:0",
                        "duration_ms": 2.0,
                    }
                ],
            },
        }
        summarized = summarize_benchmarks([benchmark], metrics)[0]["summary"]
        self.assertEqual(
            summarized["before"]["peak_execution_memory_bytes"]["median"],
            15,
        )
        self.assertEqual(summarized["after"]["jvm_gc_time_ms"]["median"], 0)


if __name__ == "__main__":
    unittest.main()
