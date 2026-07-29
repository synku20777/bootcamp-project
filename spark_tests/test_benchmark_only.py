from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.spark_pipeline.benchmark import BenchmarkCorrectnessError
from app.spark_pipeline.pipeline import (
    BenchmarkSuiteResult,
    SparkPipelineError,
    _document_sha256,
    _quality_from_bronze_manifest,
    _quality_summary,
    run_benchmark,
)


def _manifest() -> dict[str, object]:
    return {
        "manifest_version": 2,
        "source_batch_id": "fixture-source",
        "source_batch_sha256": "a" * 64,
        "source_files": {},
        "ingestion_id": "bronze-fixture",
        "datasets": ["ecdc", "indicators", "mapping", "population"],
        "quality_summary": {
            "ruleset_version": "bronze-quality-v1",
            "status": "WARN",
            "quality_document_sha256": "b" * 64,
        },
    }


def _benchmark_result() -> list[dict[str, object]]:
    measurement = {
        "repetition": 0,
        "job_group": "fixture",
        "duration_ms": 1.0,
    }
    return [
        {
            "name": "fixture",
            "correctness": {"status": "PASS", "datasets": []},
            "warmup_repetitions": 1,
            "measured_repetitions": 1,
            "measurements": {
                "before": [dict(measurement)],
                "after": [dict(measurement)],
            },
        }
    ]


class BenchmarkOnlyManifestTests(unittest.TestCase):
    def test_quality_summary_hashes_the_canonical_quality_document(self) -> None:
        quality = {
            "ruleset_version": "bronze-quality-v1",
            "status": "PASS",
            "checks": [],
        }
        summary = _quality_summary(quality)
        self.assertEqual(summary["quality_document_sha256"], _document_sha256(quality))

    def test_benchmark_only_uses_manifest_quality_without_previous_outputs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            ingestion = root / "bronze" / "ingestion_id=bronze-fixture"
            ingestion.mkdir(parents=True)
            (ingestion / "manifest.json").write_text(
                json.dumps(_manifest()),
                encoding="utf-8",
            )
            evidence_path = root / "reports" / "evidence.json"
            fake_spark = MagicMock()
            environment = {
                "python": "3.12.13",
                "pyspark": "3.5.6",
                "java": "17.0.19",
            }
            context_equivalence = {
                "status": "PASS",
                "spark": {"row_count": 1, "sha256": "a" * 64},
            }

            with (
                patch(
                    "app.spark_pipeline.pipeline.create_spark_session",
                    return_value=fake_spark,
                ),
                patch(
                    "app.spark_pipeline.pipeline._environment", return_value=environment
                ),
                patch("app.spark_pipeline.pipeline._read_bronze", return_value={}),
                patch(
                    "app.spark_pipeline.pipeline._benchmark_suite",
                    return_value=BenchmarkSuiteResult(
                        benchmarks=_benchmark_result(),
                        curated=MagicMock(),
                        layout={},
                        plans={},
                        context_equivalence=context_equivalence,
                    ),
                ),
                patch("app.spark_pipeline.pipeline.parse_event_logs", return_value={}),
            ):
                run_benchmark(
                    ingestion_id="bronze-fixture",
                    benchmark_run_id="benchmark-fixture",
                    bronze_root=root / "bronze",
                    output_root=root / "outputs",
                    evidence_path=evidence_path,
                )

            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(evidence["evidence_version"], 3)
            self.assertEqual(evidence["quality"], _manifest()["quality_summary"])
            self.assertEqual(
                evidence["country_context_equivalence"], context_equivalence
            )
            self.assertFalse(any((root / "outputs").rglob("quality.json")))
            fake_spark.catalog.clearCache.assert_called_once_with()
            fake_spark.stop.assert_called_once_with()

    def test_legacy_manifest_is_rejected_before_spark_initialization(self) -> None:
        manifest = _manifest()
        manifest["manifest_version"] = 1
        with self.assertRaisesRegex(
            SparkPipelineError,
            "requires Bronze manifest version 2",
        ):
            _quality_from_bronze_manifest(manifest)

    def test_failed_suite_does_not_replace_authoritative_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            ingestion = root / "bronze" / "ingestion_id=bronze-fixture"
            ingestion.mkdir(parents=True)
            (ingestion / "manifest.json").write_text(
                json.dumps(_manifest()),
                encoding="utf-8",
            )
            evidence_path = root / "reports" / "evidence.json"
            evidence_path.parent.mkdir(parents=True)
            evidence_path.write_text("authoritative", encoding="utf-8")
            fake_spark = MagicMock()

            with (
                patch(
                    "app.spark_pipeline.pipeline.create_spark_session",
                    return_value=fake_spark,
                ),
                patch("app.spark_pipeline.pipeline._environment", return_value={}),
                patch("app.spark_pipeline.pipeline._read_bronze", return_value={}),
                patch(
                    "app.spark_pipeline.pipeline._benchmark_suite",
                    side_effect=BenchmarkCorrectnessError("failed gate"),
                ),
            ):
                with self.assertRaises(BenchmarkCorrectnessError):
                    run_benchmark(
                        ingestion_id="bronze-fixture",
                        benchmark_run_id="benchmark-failed",
                        bronze_root=root / "bronze",
                        output_root=root / "outputs",
                        evidence_path=evidence_path,
                    )

            self.assertEqual(evidence_path.read_text(encoding="utf-8"), "authoritative")
            fake_spark.catalog.clearCache.assert_called_once_with()
            fake_spark.stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
