from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from uuid import uuid4

from scripts.export_spark_sources import export_source_batch


class FakeCursor:
    def __init__(self) -> None:
        self.executions = 0
        self.description = []
        self._pending: list[list[tuple[object, ...]]] = []

    def execute(self, _query: str) -> None:
        self.executions += 1
        if self.executions == 1:
            self.description = [
                ("COUNTRY_REGION",),
                ("ISO3166_1",),
                ("REPORT_DATE",),
                ("CASES",),
                ("DEATHS",),
            ]
            self._pending = [
                [("Latvia", "LV", "2020-03-01", 1, 0)],
                [],
            ]
        elif self.executions == 2:
            self.description = [
                ("SOURCE_COUNTRY_NAME",),
                ("SOURCE_COUNTRY_CODE",),
                ("NORMALIZED_COUNTRY_NAME",),
                ("NORMALIZED_ISO2",),
                ("NORMALIZED_ISO3",),
                ("EXPECTED_POPULATION_MATCH",),
            ]
            self._pending = [
                [("Namibia", None, "Namibia", "NA", "NAM", True)],
                [],
            ]
        elif self.executions == 3:
            self.description = [
                ("COUNTRY",),
                ("COUNTRY_ISO2",),
                ("COUNTRY_ISO3",),
                ("LOCATION_KEY",),
                ("REPORT_DATE",),
                ("NEW_CASES_RAW",),
                ("NEW_DEATHS_RAW",),
                ("CASES_CUMULATIVE",),
                ("DEATHS_CUMULATIVE",),
                ("COVID_RATE_POPULATION_2020",),
                ("NEW_CASES_PER_100K",),
                ("NEW_DEATHS_PER_100K",),
                ("CASES_PER_100K",),
                ("DEATHS_PER_100K",),
                ("HAS_NEGATIVE_CASE_CORRECTION",),
                ("HAS_NEGATIVE_DEATH_CORRECTION",),
                ("SOURCE_NAME",),
                ("SERIES_SEGMENT",),
            ]
            self._pending = [
                [
                    (
                        "Latvia",
                        "LV",
                        "LVA",
                        "LVA",
                        "2020-03-01",
                        1,
                        0,
                        1,
                        0,
                        1_900_000,
                        0.0526,
                        0.0,
                        0.0526,
                        0.0,
                        False,
                        False,
                        "ECDC",
                        "ECDC_BASELINE",
                    )
                ],
                [],
            ]
        else:
            self.description = [
                ("ISO3",),
                ("POPULATION_2020_CONTEXT",),
                ("POPULATION_DENSITY_2019",),
                ("POPULATION_AGE_65_PLUS_PCT_2019",),
                ("REAL_GDP_PER_CAPITA_2019",),
                ("HEALTH_EXPENDITURE_PER_CAPITA_PPP_2019",),
                ("SNAPSHOT_ID",),
            ]
            self._pending = [
                [
                    (
                        "LVA",
                        1_900_449,
                        30.75,
                        20.4,
                        15_328.38,
                        2_202.67,
                        "snapshot",
                    )
                ],
                [],
            ]

    def fetchmany(self, _size: int):
        return self._pending.pop(0)

    def close(self) -> None:
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = FakeCursor()

    def cursor(self) -> FakeCursor:
        return self.cursor_instance


class SparkSourceExportTests(unittest.TestCase):
    def test_direct_script_invocation_can_import_application_package(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                str(repository_root / "scripts" / "export_spark_sources.py"),
                "--help",
            ],
            cwd=repository_root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--source-batch-id", result.stdout)

    def test_export_includes_cross_engine_context_fingerprint(self) -> None:
        root = Path("outputs/test-spark-export") / uuid4().hex
        root.mkdir(parents=True)
        try:
            population = root / "population.csv"
            with population.open("w", encoding="utf-8", newline="") as output:
                writer = csv.writer(output)
                writer.writerow(
                    [
                        "COUNTRY_CODE_ISO2",
                        "COUNTRY_CODE_ISO3",
                        "COUNTRY_NAME",
                        "POPULATION",
                        "POPULATION_YEAR",
                    ]
                )
                writer.writerow(["LV", "LVA", "Latvia", 1_900_000, 2020])

            connection = FakeConnection()
            target = export_source_batch(
                source_batch_id="fixture-v1",
                output_root=root / "source",
                population_path=population,
                connection=connection,
            )
            manifest = json.loads(
                (target / "manifest.json").read_text(encoding="utf-8")
            )

            self.assertEqual(connection.cursor_instance.executions, 4)
            self.assertEqual(manifest["manifest_version"], 3)
            self.assertEqual(manifest["source_kind"], "snowflake_export")
            self.assertEqual(manifest["files"]["ecdc"]["row_count"], 1)
            self.assertEqual(manifest["files"]["mapping"]["row_count"], 1)
            self.assertEqual(manifest["files"]["covid_extended"]["country_count"], 1)
            self.assertEqual(manifest["files"]["covid_extended"]["row_count"], 1)
            self.assertEqual(
                manifest["files"]["covid_extended"]["minimum_report_date"],
                "2020-03-01",
            )
            self.assertEqual(
                manifest["files"]["covid_extended"]["maximum_report_date"],
                "2020-03-01",
            )
            self.assertEqual(
                manifest["sources"]["covid_extended"],
                "COVID_ANALYTICS.MARTS.COVID_ENRICHED_EXTENDED",
            )
            self.assertEqual(len(manifest["files"]["covid_extended"]["sha256"]), 64)
            self.assertEqual(len(manifest["batch_sha256"]), 64)
            self.assertEqual(manifest["snowflake_context_fingerprint"]["row_count"], 1)
            with self.assertRaises(FileExistsError):
                export_source_batch(
                    source_batch_id="fixture-v1",
                    output_root=root / "source",
                    population_path=population,
                    connection=connection,
                )
        finally:
            shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
