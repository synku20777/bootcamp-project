from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.spark_pipeline.benchmark import BenchmarkCorrectnessError
from app.spark_pipeline.clustering import ClusteringValidationError
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
        "manifest_version": 3,
        "source_kind": "fixture",
        "source_batch_id": "fixture-source",
        "source_batch_sha256": "a" * 64,
        "source_files": {},
        "ingestion_id": "bronze-fixture",
        "datasets": [
            "covid_extended",
            "ecdc",
            "indicators",
            "mapping",
            "population",
        ],
        "quality_summary": {
            "ruleset_version": "bronze-quality-v2",
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
            "ruleset_version": "bronze-quality-v2",
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
            evidence_path.parent.mkdir(parents=True)
            evidence_path.write_text("authoritative-v3", encoding="utf-8")
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
                        broadcast_decisions={
                            "mapping": True,
                            "population": True,
                            "indicators": True,
                        },
                        skew={"status": "PASS"},
                    ),
                ),
                patch(
                    "app.spark_pipeline.pipeline._clustering_stage",
                    return_value={"diagnostics_version": 1},
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

            preview_path = (
                root / "outputs" / "benchmark-fixture" / "evidence.preview.json"
            )
            evidence = json.loads(preview_path.read_text(encoding="utf-8"))
            self.assertEqual(evidence["evidence_version"], 4)
            self.assertEqual(evidence["quality"], _manifest()["quality_summary"])
            self.assertEqual(
                evidence["country_context_equivalence"], context_equivalence
            )
            self.assertFalse(any((root / "outputs").rglob("quality.json")))
            self.assertEqual(
                evidence_path.read_text(encoding="utf-8"), "authoritative-v3"
            )
            fake_spark.catalog.clearCache.assert_called_once_with()
            fake_spark.stop.assert_called_once_with()

    def test_legacy_manifest_is_rejected_before_spark_initialization(self) -> None:
        manifest = _manifest()
        manifest["manifest_version"] = 2
        with self.assertRaisesRegex(
            SparkPipelineError,
            "requires Bronze manifest version 3",
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

    def test_failed_model_selection_does_not_replace_authoritative_evidence(
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
                    return_value=BenchmarkSuiteResult(
                        benchmarks=_benchmark_result(),
                        curated=MagicMock(),
                        layout={},
                        plans={},
                        context_equivalence={},
                        broadcast_decisions={
                            "mapping": False,
                            "population": False,
                            "indicators": False,
                        },
                        skew={"status": "PASS"},
                    ),
                ),
                patch(
                    "app.spark_pipeline.pipeline._clustering_stage",
                    side_effect=ClusteringValidationError("unstable fixture"),
                ),
            ):
                with self.assertRaises(ClusteringValidationError):
                    run_benchmark(
                        ingestion_id="bronze-fixture",
                        benchmark_run_id="benchmark-unstable",
                        bronze_root=root / "bronze",
                        output_root=root / "outputs",
                        evidence_path=evidence_path,
                    )

            self.assertEqual(evidence_path.read_text(encoding="utf-8"), "authoritative")
            fake_spark.catalog.clearCache.assert_called_once_with()
            fake_spark.stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
