from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.spark_pipeline.pipeline import run_ingest_profile
from spark_tests.source_batch_fixture import write_pipeline_source_batch
from spark_tests.spark_test_support import install_pyspark_socket_warning_filter


class FixturePipelineTests(unittest.TestCase):
    def test_complete_fixture_pipeline_keeps_authoritative_evidence(self) -> None:
        install_pyspark_socket_warning_filter()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_batch_id = "cluster-pipeline-fixture"
            write_pipeline_source_batch(root / "source" / source_batch_id)
            evidence = root / "reports" / "spark" / "evidence.json"
            evidence.parent.mkdir(parents=True)
            evidence.write_text("authoritative-v3", encoding="utf-8")

            run_ingest_profile(
                source_batch_id=source_batch_id,
                ingestion_id="fixture-bronze-v1",
                benchmark_run_id="fixture-benchmark-v1",
                source_root=root / "source",
                bronze_root=root / "bronze",
                curated_root=root / "curated",
                output_root=root / "outputs",
                evidence_path=evidence,
                clustering_diagnostics_path=(
                    root / "reports" / "spark" / "clustering_diagnostics.json"
                ),
                exact_distinct_max_rows=1_000_000,
                shuffle_partitions=4,
                cluster_min_observations=14,
                cluster_k_min=2,
                cluster_k_max=2,
            )

            run_output = root / "outputs" / "fixture-benchmark-v1"
            preview = json.loads(
                (run_output / "evidence.preview.json").read_text(encoding="utf-8")
            )
            clustering = json.loads(
                (run_output / "clustering_diagnostics.preview.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(preview["evidence_version"], 4)
            self.assertEqual(clustering["selection"]["selected_k"], 2)
            self.assertEqual(
                preview["layout"]["monthly_measurement"],
                "actual_partition_directories",
            )
            self.assertEqual(evidence.read_text(encoding="utf-8"), "authoritative-v3")
            self.assertFalse(
                (root / "reports" / "spark" / "clustering_diagnostics.json").exists()
            )
            self.assertTrue(
                (
                    run_output
                    / "clustering"
                    / "model_id=fixture-benchmark-v1"
                    / "assignments"
                ).is_dir()
            )
            self.assertTrue(
                (
                    run_output
                    / "clustering"
                    / "model_id=fixture-benchmark-v1"
                    / "model"
                    / "kmeans"
                ).is_dir()
            )
            self.assertTrue(
                (root / "curated" / "ingestion_id=fixture-bronze-v1").is_dir()
            )
            bronze_manifest = json.loads(
                (
                    root / "bronze" / "ingestion_id=fixture-bronze-v1" / "manifest.json"
                ).read_text(encoding="utf-8")
            )
            extended_layout = bronze_manifest["bronze_publication_layout"][
                "covid_extended"
            ]
            self.assertEqual(extended_layout["target_file_count"], 1)
            self.assertEqual(extended_layout["records"]["output_file_count"], 1)


if __name__ == "__main__":
    unittest.main()
