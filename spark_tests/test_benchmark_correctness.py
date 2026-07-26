from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pyspark.sql import SparkSession

from app.spark_pipeline.benchmark import (
    BenchmarkCorrectnessError,
    BenchmarkRunner,
    CorrectnessOutputs,
    dataframe_fingerprint,
)
from app.spark_pipeline.pipeline import _cache_correctness, _write_correctness


class BenchmarkCorrectnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("benchmark-correctness-tests")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.session.timeZone", "UTC")
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.spark.stop()

    def test_fingerprint_is_order_independent_and_duplicate_sensitive(self) -> None:
        dataframe = self.spark.createDataFrame(
            [(1, "alpha"), (2, None), (1, "alpha")],
            "id int, value string",
        )
        reordered = dataframe.repartition(2).orderBy("id", ascending=False)
        with_extra_duplicate = dataframe.unionByName(
            self.spark.createDataFrame([(1, "alpha")], "id int, value string")
        )

        original = dataframe_fingerprint(dataframe)
        self.assertEqual(original, dataframe_fingerprint(reordered))
        duplicate = dataframe_fingerprint(with_extra_duplicate)
        self.assertEqual(duplicate["row_count"], original["row_count"] + 1)
        self.assertNotEqual(duplicate["content_sha256"], original["content_sha256"])

    def test_gate_records_equal_count_content_failure_before_timing(self) -> None:
        before = self.spark.createDataFrame([(1, "alpha")], "id int, value string")
        after = self.spark.createDataFrame([(1, "beta")], "id int, value string")
        timed_calls = {"before": 0, "after": 0}
        original_aqe = self.spark.conf.get("spark.sql.adaptive.enabled")

        def timed(variant: str):
            def action():
                timed_calls[variant] += 1
                return {}

            return action

        with tempfile.TemporaryDirectory() as temporary_directory:
            failure_path = Path(temporary_directory) / "correctness_failure.json"
            runner = BenchmarkRunner(self.spark, "failure-v1", failure_path)
            with self.assertRaises(BenchmarkCorrectnessError):
                runner.compare(
                    name="changed_value",
                    before=timed("before"),
                    after=timed("after"),
                    correctness_before=lambda: CorrectnessOutputs(
                        {"logical_result": before}
                    ),
                    correctness_after=lambda: CorrectnessOutputs(
                        {"logical_result": after}
                    ),
                    before_configuration={"spark.sql.adaptive.enabled": "false"},
                    after_configuration={"spark.sql.adaptive.enabled": "true"},
                )

            self.assertEqual(timed_calls, {"before": 0, "after": 0})
            self.assertEqual(
                self.spark.conf.get("spark.sql.adaptive.enabled"), original_aqe
            )
            failure = json.loads(failure_path.read_text(encoding="utf-8"))
            dataset = failure["datasets"][0]
            self.assertEqual(failure["benchmark_name"], "changed_value")
            self.assertEqual(dataset["dataset"], "logical_result")
            self.assertTrue(dataset["schema_match"])
            self.assertTrue(dataset["row_count_match"])
            self.assertFalse(dataset["content_checksum_match"])
            self.assertFalse(dataset["passed"])

    def test_schema_name_order_and_type_are_part_of_the_fingerprint(self) -> None:
        baseline = self.spark.createDataFrame([(1,)], "value int")
        renamed = self.spark.createDataFrame([(1,)], "renamed int")
        reordered = self.spark.createDataFrame([(1, 2)], "first int, second int")
        reordered_columns = reordered.select("second", "first")
        changed_type = self.spark.createDataFrame([(1,)], "value long")

        baseline_fingerprint = dataframe_fingerprint(baseline)
        self.assertNotEqual(
            baseline_fingerprint["schema_sha256"],
            dataframe_fingerprint(renamed)["schema_sha256"],
        )
        self.assertNotEqual(
            dataframe_fingerprint(reordered)["schema_sha256"],
            dataframe_fingerprint(reordered_columns)["schema_sha256"],
        )
        self.assertNotEqual(
            baseline_fingerprint["schema_sha256"],
            dataframe_fingerprint(changed_type)["schema_sha256"],
        )

    def test_cache_and_file_layout_use_named_readable_outputs(self) -> None:
        ecdc = self.spark.createDataFrame(
            [("Latvia", "2020-03-01", 1.0, 0.0)],
            "COUNTRY_REGION string, REPORT_DATE string, CASES double, DEATHS double",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            cache_runner = BenchmarkRunner(
                self.spark,
                "cache-v1",
                root / "cache_failure.json",
            )
            cache_result = cache_runner.compare(
                name="cache",
                before=lambda: {},
                after=lambda: {},
                correctness_before=lambda: _cache_correctness(
                    ecdc, cache_enabled=False
                ),
                correctness_after=lambda: _cache_correctness(ecdc, cache_enabled=True),
            )
            self.assertEqual(
                [item["dataset"] for item in cache_result["correctness"]["datasets"]],
                ["country_counts", "totals"],
            )

            layout_runner = BenchmarkRunner(
                self.spark,
                "layout-v1",
                root / "layout_failure.json",
            )
            layout_result = layout_runner.compare(
                name="layout",
                before=lambda: {},
                after=lambda: {},
                correctness_before=lambda: _write_correctness(
                    self.spark,
                    ecdc,
                    root,
                    partitions=2,
                    repartition=True,
                ),
                correctness_after=lambda: _write_correctness(
                    self.spark,
                    ecdc,
                    root,
                    partitions=1,
                    repartition=False,
                ),
            )
            self.assertEqual(layout_result["correctness"]["status"], "PASS")
            self.assertFalse(any(root.glob("correctness-*")))


if __name__ == "__main__":
    unittest.main()
