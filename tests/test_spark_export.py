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
        else:
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

    def test_export_uses_two_queries_and_writes_an_immutable_manifest(self) -> None:
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

            self.assertEqual(connection.cursor_instance.executions, 2)
            self.assertEqual(manifest["files"]["ecdc"]["row_count"], 1)
            self.assertEqual(manifest["files"]["mapping"]["row_count"], 1)
            self.assertEqual(len(manifest["batch_sha256"]), 64)
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
