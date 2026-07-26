from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.spark_pipeline.pipeline import QualityFailure, run_ingest_profile


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
                self._assert_schema_failure(Path(temp), case)

    def _assert_schema_failure(self, root: Path, case: dict[str, object]) -> None:
        source_batch_id = f"schema-{case['name']}"
        ingestion_id = f"bronze-{case['name']}"
        benchmark_run_id = f"benchmark-{case['name']}"
        source_root = root / "source"
        source_directory = source_root / source_batch_id
        source_directory.mkdir(parents=True)
        batch_sha256 = self._write_checksum_valid_batch(source_directory, case)

        bronze_root = root / "bronze"
        curated_root = root / "curated"
        output_root = root / "outputs"
        evidence_path = root / "reports" / "evidence.json"

        with patch(
            "app.spark_pipeline.pipeline.create_spark_session"
        ) as create_spark_session:
            with self.assertRaisesRegex(
                QualityFailure,
                "Schema drift blocked Bronze publication",
            ):
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
        self.assertEqual(quality["source_batch_sha256"], batch_sha256)
        self.assertEqual(quality["ingestion_id"], ingestion_id)
        self.assertEqual(quality["profiles"], {})
        self.assertEqual(
            quality["publication"],
            {"bronze": False, "curated": False},
        )

        ecdc_header = quality["headers"]["ecdc"]
        self.assertEqual(ecdc_header["missing"], case["missing"])
        self.assertEqual(ecdc_header["unexpected"], case["unexpected"])
        self.assertEqual(ecdc_header["reordered"], case["reordered"])
        self.assertFalse(ecdc_header["matches"])
        failed_checks = [check for check in quality["checks"] if not check["passed"]]
        self.assertEqual(
            [check["rule"] for check in failed_checks], ["ecdc.schema_exact"]
        )

        self.assertFalse((bronze_root / f"ingestion_id={ingestion_id}").exists())
        self.assertFalse((curated_root / f"ingestion_id={ingestion_id}").exists())
        self.assertFalse((run_output / "eventlog").exists())
        self.assertFalse((run_output / "plans").exists())
        self.assertFalse((run_output / "optimization_metrics.json").exists())
        self.assertFalse(evidence_path.exists())

    def _write_checksum_valid_batch(
        self,
        source_directory: Path,
        case: dict[str, object],
    ) -> str:
        contents = {
            "ecdc": (
                "ecdc_global.csv",
                f"{case['header']}\n{case['row']}\n",
            ),
            "mapping": (
                "country_mapping.csv",
                "SOURCE_COUNTRY_NAME,SOURCE_COUNTRY_CODE,"
                "NORMALIZED_COUNTRY_NAME,NORMALIZED_ISO2,NORMALIZED_ISO3,"
                "EXPECTED_POPULATION_MATCH\nLatvia,LV,Latvia,LV,LVA,true\n",
            ),
            "population": (
                "population.csv",
                "COUNTRY_CODE_ISO2,COUNTRY_CODE_ISO3,COUNTRY_NAME,POPULATION,"
                "POPULATION_YEAR\nLV,LVA,Latvia,1900000,2020\n",
            ),
        }
        files: dict[str, dict[str, object]] = {}
        for name, (filename, content) in contents.items():
            path = source_directory / filename
            path.write_text(content, encoding="utf-8")
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            files[name] = {
                "filename": filename,
                "row_count": 1,
                "byte_count": path.stat().st_size,
                "sha256": checksum,
            }

        checksum_payload = "".join(str(files[name]["sha256"]) for name in sorted(files))
        batch_sha256 = hashlib.sha256(checksum_payload.encode("ascii")).hexdigest()
        manifest = {
            "manifest_version": 1,
            "source_batch_id": source_directory.name,
            "files": files,
            "batch_sha256": batch_sha256,
        }
        (source_directory / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return batch_sha256


if __name__ == "__main__":
    unittest.main()
