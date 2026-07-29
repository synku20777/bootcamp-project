from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.spark_pipeline.pipeline import (
    QualityFailure,
    SourceValidationError,
    run_ingest_profile,
)
from spark_tests.source_batch_fixture import write_source_batch


class SchemaDriftFailureTests(unittest.TestCase):
    def test_schema_drift_publishes_only_quality_without_starting_spark(self) -> None:
        cases = (
            {
                "name": "renamed",
                "header": "COUNTRY_REGION,ISO3166_1,DATE,CASES,DEATHS",
                "row": "Latvia,LV,2020-03-01,1,0",
                "missing": ["REPORT_DATE"],
                "unexpected": ["DATE"],
                "reordered": False,
            },
            {
                "name": "reordered",
                "header": "ISO3166_1,COUNTRY_REGION,REPORT_DATE,CASES,DEATHS",
                "row": "LV,Latvia,2020-03-01,1,0",
                "missing": [],
                "unexpected": [],
                "reordered": True,
            },
        )

        for case in cases:
            with self.subTest(case=case["name"]), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source_batch_id = f"schema-{case['name']}"
                source_directory = root / "source" / source_batch_id
                manifest = write_source_batch(
                    source_directory,
                    ecdc_header=str(case["header"]),
                    ecdc_row=str(case["row"]),
                )

                quality = self._run_pre_spark_failure(
                    root,
                    source_batch_id=source_batch_id,
                    manifest=manifest,
                    expected_error=QualityFailure,
                    expected_message="Schema drift blocked Bronze publication",
                )
                ecdc_header = quality["headers"]["ecdc"]
                self.assertEqual(ecdc_header["missing"], case["missing"])
                self.assertEqual(ecdc_header["unexpected"], case["unexpected"])
                self.assertEqual(ecdc_header["reordered"], case["reordered"])
                self.assertFalse(ecdc_header["matches"])
                self.assertEqual(
                    self._failed_rules(quality),
                    [("ecdc.schema_exact", None)],
                )

    def test_missing_required_source_publishes_quality_without_spark(self) -> None:
        cases = (
            {
                "name": "declared",
                "missing_files": frozenset({"indicators"}),
                "undeclared_files": frozenset(),
                "failed_rules": [("required_source_file_present", "indicators")],
            },
            {
                "name": "undeclared",
                "missing_files": frozenset(),
                "undeclared_files": frozenset({"indicators"}),
                "failed_rules": [
                    ("required_source_declared", "indicators"),
                    ("required_source_file_present", "indicators"),
                ],
            },
        )

        for case in cases:
            with self.subTest(case=case["name"]), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source_batch_id = f"missing-{case['name']}"
                manifest = write_source_batch(
                    root / "source" / source_batch_id,
                    missing_files=case["missing_files"],
                    undeclared_files=case["undeclared_files"],
                )

                quality = self._run_pre_spark_failure(
                    root,
                    source_batch_id=source_batch_id,
                    manifest=manifest,
                    expected_error=SourceValidationError,
                    expected_message="Source validation blocked Bronze publication",
                )
                self.assertFalse(quality["headers"]["indicators"]["present"])
                self.assertEqual(self._failed_rules(quality), case["failed_rules"])

    def test_checksum_mismatch_publishes_quality_without_spark(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_batch_id = "checksum-mismatch"
            source_directory = root / "source" / source_batch_id
            manifest = write_source_batch(source_directory)
            indicators = source_directory / "world_bank_indicators.csv"
            indicators.write_text(
                indicators.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )

            quality = self._run_pre_spark_failure(
                root,
                source_batch_id=source_batch_id,
                manifest=manifest,
                expected_error=SourceValidationError,
                expected_message="Source validation blocked Bronze publication",
            )
            self.assertEqual(
                self._failed_rules(quality),
                [("source_file_checksum_matches", "indicators")],
            )

    def test_batch_checksum_mismatch_publishes_quality_without_spark(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_batch_id = "batch-checksum-mismatch"
            source_directory = root / "source" / source_batch_id
            manifest = write_source_batch(source_directory)
            manifest["batch_sha256"] = "0" * 64
            (source_directory / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True),
                encoding="utf-8",
            )

            quality = self._run_pre_spark_failure(
                root,
                source_batch_id=source_batch_id,
                manifest=manifest,
                expected_error=SourceValidationError,
                expected_message="Source validation blocked Bronze publication",
            )
            self.assertEqual(
                self._failed_rules(quality),
                [("source_batch_checksum_matches", "manifest")],
            )

    def _run_pre_spark_failure(
        self,
        root: Path,
        *,
        source_batch_id: str,
        manifest: dict[str, object],
        expected_error: type[QualityFailure],
        expected_message: str,
    ) -> dict[str, object]:
        ingestion_id = f"bronze-{source_batch_id}"
        benchmark_run_id = f"benchmark-{source_batch_id}"
        source_root = root / "source"
        bronze_root = root / "bronze"
        curated_root = root / "curated"
        output_root = root / "outputs"
        evidence_path = root / "reports" / "evidence.json"

        with patch(
            "app.spark_pipeline.pipeline.create_spark_session"
        ) as create_spark_session:
            with self.assertRaisesRegex(expected_error, expected_message):
                run_ingest_profile(
                    source_batch_id=source_batch_id,
                    ingestion_id=ingestion_id,
                    benchmark_run_id=benchmark_run_id,
                    source_root=source_root,
                    bronze_root=bronze_root,
                    curated_root=curated_root,
                    output_root=output_root,
                    evidence_path=evidence_path,
                    exact_distinct_max_rows=1_000_000,
                )
            create_spark_session.assert_not_called()

        run_output = output_root / benchmark_run_id
        self.assertEqual(
            sorted(path.name for path in run_output.iterdir()),
            ["quality.json"],
        )
        quality = json.loads((run_output / "quality.json").read_text("utf-8"))
        self.assertEqual(quality["ruleset_version"], "bronze-quality-v1")
        self.assertEqual(quality["status"], "FAIL")
        self.assertEqual(quality["source_batch_id"], source_batch_id)
        self.assertEqual(quality["source_batch_sha256"], manifest["batch_sha256"])
        self.assertEqual(quality["ingestion_id"], ingestion_id)
        self.assertEqual(quality["profiles"], {})
        self.assertEqual(
            quality["publication"],
            {"bronze": False, "curated": False},
        )
        self.assertFalse((bronze_root / f"ingestion_id={ingestion_id}").exists())
        self.assertFalse((curated_root / f"ingestion_id={ingestion_id}").exists())
        self.assertFalse((run_output / "eventlog").exists())
        self.assertFalse((run_output / "plans").exists())
        self.assertFalse((run_output / "optimization_metrics.json").exists())
        self.assertFalse(evidence_path.exists())
        return quality

    @staticmethod
    def _failed_rules(quality: dict[str, object]) -> list[tuple[str, str | None]]:
        return [
            (str(check["rule"]), check.get("dataset"))
            for check in quality["checks"]
            if not check["passed"]
        ]


if __name__ == "__main__":
    unittest.main()
